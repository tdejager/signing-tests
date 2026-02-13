"""Orchestrate building, uploading, and deleting signing test packages."""

import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path

BASE_URL = "https://beta.prefix.dev"
CHANNEL = "signing-tests"
ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"

# Central registry of test packages: name -> subdir
PACKAGES = {
    "all-signed": "noarch",
    "last-version-unsigned": "noarch",
    "variants-unsigned": "linux-64",
}


# ---------------------------------------------------------------------------
# Build / upload helpers
# ---------------------------------------------------------------------------

def build_recipe(recipe_path, output_dir, variant_config=None, target_platform=None):
    """Build a recipe with rattler-build."""
    channel_url = f"{BASE_URL}/{CHANNEL}"
    cmd = ["rattler-build", "build", "-r", str(recipe_path), "--output-dir", str(output_dir), "--skip-existing", "all", "-c", channel_url]
    if variant_config:
        cmd += ["-m", str(variant_config)]
    if target_platform:
        cmd += ["--target-platform", target_platform]
    print(f"Building: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def upload_package(pkg_path, generate_attestation=False):
    """Upload a package to prefix.dev."""
    cmd = [
        "rattler-build", "upload", "prefix",
        "-c", CHANNEL,
        "-u", BASE_URL,
    ]
    if generate_attestation:
        cmd.append("--generate-attestation")
    cmd.append(str(pkg_path))
    print(f"Uploading: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


# ---------------------------------------------------------------------------
# Delete helpers
# ---------------------------------------------------------------------------

def load_env():
    """Load variables from .env file at the project root if it exists."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def get_api_key():
    """Get the API key from .env or the PREFIX_API_KEY environment variable."""
    load_env()
    api_key = os.environ.get("PREFIX_API_KEY")
    if not api_key:
        print("Error: PREFIX_API_KEY not found in .env or environment")
        sys.exit(1)
    return api_key


def list_packages(subdir, package_name):
    """List all packages matching a name from the channel's repodata."""
    repodata_url = f"{BASE_URL}/{CHANNEL}/{subdir}/repodata.json"
    print(f"Fetching repodata from {repodata_url}")
    req = urllib.request.Request(repodata_url, headers={"User-Agent": "signing-tests"})
    with urllib.request.urlopen(req) as resp:
        repodata = json.loads(resp.read())

    matching = []
    for filename, info in repodata.get("packages.conda", {}).items():
        if info["name"] == package_name:
            matching.append(filename)
    return sorted(matching)


def delete_package(subdir, package_filename, api_key):
    """Delete a single package from the channel via the REST API."""
    url = f"{BASE_URL}/api/v1/delete/{CHANNEL}/{subdir}/{package_filename}"
    print(f"Deleting: {url}")
    req = urllib.request.Request(url, method="DELETE")
    req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req) as resp:
            print(f"  -> {resp.status} {resp.reason}")
    except urllib.error.HTTPError as e:
        print(f"  -> {e.code} {e.reason}")
        if e.code != 404:
            raise


def delete_packages(name):
    """Delete all packages for a given test package name."""
    subdir = PACKAGES[name]
    api_key = get_api_key()
    filenames = list_packages(subdir, name)
    if not filenames:
        print(f"No packages found for {name!r} in {subdir}")
        return
    for filename in filenames:
        delete_package(subdir, filename, api_key)


# ---------------------------------------------------------------------------
# Publish handlers
# ---------------------------------------------------------------------------

def publish_all_signed():
    """Build v1 + v2, upload both with attestation."""
    name = "all-signed"
    output_dir = OUTPUT_DIR / name
    recipes = ROOT / "recipes" / name

    for version_dir in sorted(recipes.iterdir()):
        if not version_dir.is_dir():
            continue
        build_recipe(version_dir / "recipe.yaml", output_dir)

    for pkg in sorted(output_dir.rglob("*.conda")):
        upload_package(pkg, generate_attestation=True)


def publish_last_version_unsigned():
    """Build v1 + v1.5 + v2, upload v1 and v2 signed, then v1.5 unsigned last.

    Tests a bug where the channel stays "verified" because v2.0.0 (the latest
    version) is signed, even though v1.5.0 (uploaded last) is unsigned.
    """
    name = "last-version-unsigned"
    output_dir = OUTPUT_DIR / name
    recipes = ROOT / "recipes" / name

    for version_dir in sorted(recipes.iterdir()):
        if not version_dir.is_dir():
            continue
        build_recipe(version_dir / "recipe.yaml", output_dir)

    packages = sorted(output_dir.rglob("*.conda"))

    # Upload signed versions first (v1.0.0, v2.0.0), then unsigned (v1.5.0) last
    signed_pkgs = [p for p in packages if "1.5.0" not in p.name]
    unsigned_pkgs = [p for p in packages if "1.5.0" in p.name]

    for pkg in signed_pkgs:
        upload_package(pkg, generate_attestation=True)
    for pkg in unsigned_pkgs:
        upload_package(pkg, generate_attestation=False)


def publish_variants_unsigned():
    """Build both Python variants, upload py312 with attestation, py313 without."""
    name = "variants-unsigned"
    output_dir = OUTPUT_DIR / name
    recipe = ROOT / "recipes" / name / "recipe.yaml"
    variants = ROOT / "recipes" / name / "variants.yaml"

    build_recipe(recipe, output_dir, variant_config=variants, target_platform="linux-64")

    for pkg in sorted(output_dir.rglob("*.conda")):
        signed = "py312" in pkg.name
        upload_package(pkg, generate_attestation=signed)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    actions = {
        "publish": {
            "all-signed": publish_all_signed,
            "last-version-unsigned": publish_last_version_unsigned,
            "variants-unsigned": publish_variants_unsigned,
        },
        "delete": {name: (lambda n=name: delete_packages(n)) for name in PACKAGES},
    }

    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <publish|delete> <{'|'.join(PACKAGES)}|all>")
        sys.exit(1)

    action = sys.argv[1]
    if action not in actions:
        print(f"Unknown action: {action}")
        print(f"Choose from: {', '.join(actions)}")
        sys.exit(1)

    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} {action} <{'|'.join(PACKAGES)}|all>")
        sys.exit(1)

    target = sys.argv[2]
    handlers = actions[action]

    if target == "all":
        for handler in handlers.values():
            handler()
    elif target in handlers:
        handlers[target]()
    else:
        print(f"Unknown package: {target}")
        print(f"Choose from: {', '.join(handlers)}, all")
        sys.exit(1)


if __name__ == "__main__":
    main()
