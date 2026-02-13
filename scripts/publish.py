"""Orchestrate building and uploading signing test packages."""

import subprocess
import sys
from pathlib import Path

BASE_URL = "https://beta.prefix.dev"
CHANNEL = "signing-tests"
ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"


def build_recipe(recipe_path, output_dir, variant_config=None, target_platform=None):
    """Build a recipe with rattler-build."""
    channel_url = f"{BASE_URL}/channels/{CHANNEL}"
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


def publish_all_signed():
    """Build v1 + v2, upload both with attestation."""
    output_dir = OUTPUT_DIR / "all-signed"
    recipes = ROOT / "recipes" / "all-signed"

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
    output_dir = OUTPUT_DIR / "last-version-unsigned"
    recipes = ROOT / "recipes" / "last-version-unsigned"

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
    output_dir = OUTPUT_DIR / "variants-unsigned"
    recipe = ROOT / "recipes" / "variants-unsigned" / "recipe.yaml"
    variants = ROOT / "recipes" / "variants-unsigned" / "variants.yaml"

    build_recipe(recipe, output_dir, variant_config=variants, target_platform="linux-64")

    for pkg in sorted(output_dir.rglob("*.conda")):
        signed = "py312" in pkg.name
        upload_package(pkg, generate_attestation=signed)


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <all-signed|last-version-unsigned|variants-unsigned>")
        sys.exit(1)

    package = sys.argv[1]
    handlers = {
        "all-signed": publish_all_signed,
        "last-version-unsigned": publish_last_version_unsigned,
        "variants-unsigned": publish_variants_unsigned,
    }

    handler = handlers.get(package)
    if handler is None:
        print(f"Unknown package: {package}")
        print(f"Choose from: {', '.join(handlers)}")
        sys.exit(1)

    handler()


if __name__ == "__main__":
    main()
