r"""Build deploy/aws/build/guardoath-lambda.zip for the AWS Lambda python3.13 x86_64 runtime.

    C:\Users\<you>\AppData\Local\Programs\Python\Python313\python.exe deploy\aws\build_lambda.py

Dependencies come from pyproject.toml ([project].dependencies plus the pii and aws extras). They are
downloaded as manylinux2014_x86_64 CPython 3.13 wheels, so this works on Windows without Docker. The zip
holds them, the guardoath package, policies/ and index.py (the handler the template names:
index.handler). The build fails if the zip is too big to upload directly (50 MB) or too big for Lambda
once unpacked (250 MB).
"""
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD = Path(__file__).resolve().parent / "build"
STAGE = BUILD / "package"
ZIP = BUILD / "guardoath-lambda.zip"
DIRECT_UPLOAD_LIMIT = 50 * 1024 * 1024
UNZIPPED_LIMIT = 250 * 1024 * 1024
INDEX = "from guardoath.lambda_handler import handler  # noqa: F401  (Lambda handler: index.handler)\n"


def requirements(root: Path = ROOT) -> list[str]:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    extras = project.get("optional-dependencies", {})
    # The aws extra (pinned boto3/botocore) is bundled instead of the runtime's own copy, so the
    # versions are the tested ones.
    return list(project["dependencies"]) + list(extras.get("pii", [])) + list(extras.get("aws", []))


def package_files(stage: Path) -> list[tuple[str, Path]]:
    return [(path.relative_to(stage).as_posix(), path) for path in sorted(stage.rglob("*")) if path.is_file()]


def main() -> int:
    if BUILD.exists():
        shutil.rmtree(BUILD)
    STAGE.mkdir(parents=True)
    reqs = requirements()
    print("Downloading Linux wheels for:", ", ".join(reqs), flush=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                    "--platform", "manylinux2014_x86_64", "--implementation", "cp", "--python-version", "3.13",
                    "--only-binary=:all:", "--target", str(STAGE), *reqs], check=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(ROOT / "guardoath", STAGE / "guardoath", ignore=ignore)
    shutil.copytree(ROOT / "policies", STAGE / "policies", ignore=ignore)
    (STAGE / "index.py").write_text(INDEX, encoding="utf-8", newline="\n")

    total = 0
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, path in package_files(STAGE):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100000 | 0o644) << 16
            data = path.read_bytes()
            total += len(data)
            archive.writestr(info, data)
    zipped = ZIP.stat().st_size
    print(f"Unzipped: {total} bytes (Lambda limit {UNZIPPED_LIMIT})")
    print(f"Zip: {ZIP} {zipped} bytes (direct upload limit {DIRECT_UPLOAD_LIMIT})")
    if total > UNZIPPED_LIMIT or zipped > DIRECT_UPLOAD_LIMIT:
        print("ERROR: the package is over a Lambda size limit", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
