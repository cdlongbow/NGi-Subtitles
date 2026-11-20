"""Validate ZIP submissions and maintain the subtitle pool."""

import argparse
import hashlib
import io
import json
import lzma
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import zipfile
import zlib


REPOSITORY = "li-peifeng/NGi-Subtitles"
UPLOADER_ID = 144838180  # jzdxjk
SUBTITLES = Path("Subtitles")
INBOX = Path("update")
INDEX = Path("tree.json")
NUMBER = re.compile(r"([A-Z0-9]{1,80})-([0-9]{1,40})[.]([A-Z0-9]{1,16})", re.ASCII | re.I)
MIB = 1024 * 1024
MAX_ZIP = 50 * MIB
MAX_MEMBER = 16 * MIB
MAX_EXPANDED = 128 * MIB
MAX_ENTRIES = 10_000
ARCHIVE_ERRORS = (zipfile.BadZipFile, NotImplementedError, EOFError, lzma.LZMAError, zlib.error)


class InvalidUpload(ValueError):
    pass


def git(*args):
    return subprocess.check_output(["git", *args])


def archive_name(name):
    return (
        name.lower().endswith(".zip")
        and len(name.encode("utf-8")) <= 255
        and name.isprintable()
        and not name.startswith(".")
        and not any(character in name for character in "/\\:*?\"<>|")
    )


def subtitle_path(filename):
    match = NUMBER.fullmatch(filename)
    if match is None:
        return None
    prefix, number, extension = match.groups()
    prefix = prefix.upper()
    return SUBTITLES / prefix[0] / prefix / f"{prefix}-{number}.{extension.lower()}"


def read_archive(source):
    files = {}
    ignored = 0
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ENTRIES or sum(entry.file_size for entry in entries) > MAX_EXPANDED:
            raise InvalidUpload("ZIP exceeds the entry count or expanded size limit")

        for entry in entries:
            name = entry.orig_filename
            parts = name.removesuffix("/").split("/")
            mode = stat.S_IFMT(entry.external_attr >> 16)
            if (
                any(part in ("", ".", "..") for part in parts)
                or not name.isprintable()
                or "\\" in name
                or ":" in name
                or mode not in (0, stat.S_IFREG, stat.S_IFDIR)
                or (mode == stat.S_IFDIR and not entry.is_dir())
            ):
                raise InvalidUpload(f"invalid ZIP entry {name!r}")
            if entry.is_dir():
                continue
            if entry.flag_bits & 1:
                raise InvalidUpload(f"encrypted ZIP entry {name!r}")
            if any(part.startswith(".") or part == "__MACOSX" for part in parts):
                continue
            target = subtitle_path(parts[-1])
            if target is None:
                ignored += 1
                continue
            if not 0 < entry.file_size <= MAX_MEMBER:
                raise InvalidUpload(f"empty or oversized subtitle {name!r}")
            data = archive.read(entry)
            if target in files and files[target] != data:
                raise InvalidUpload(f"different subtitles map to {target}")
            files[target] = data

    if not files:
        raise InvalidUpload("ZIP has no subtitles named PREFIX-NUMBER.ext")
    return files, ignored


def validate_targets(files, pending):
    digests = {}
    for target, data in files.items():
        digest = hashlib.sha256(data).digest()
        if target in pending and pending[target] != digest:
            raise InvalidUpload(f"submitted ZIPs contain different versions of {target}")
        if any(path.is_symlink() for path in (target, *target.parents)):
            raise InvalidUpload(f"subtitle path contains a symlink: {target}")
        if target.exists() and (not target.is_file() or target.read_bytes() != data):
            raise InvalidUpload(f"a different file already exists at {target}")
        digests[target] = digest
    pending.update(digests)


def pull_request(number):
    return json.loads(subprocess.check_output(["gh", "api", f"repos/{REPOSITORY}/pulls/{number}"]))


def eligible(pr, head):
    return (
        pr["state"] == "open"
        and not pr["draft"]
        and pr["user"]["id"] == UPLOADER_ID
        and pr["base"]["ref"] == "main"
        and pr["base"]["repo"]["full_name"] == REPOSITORY
        and pr["head"]["sha"] == head
        and pr["head"]["repo"] is not None
        and pr["head"]["repo"]["owner"]["id"] == UPLOADER_ID
    )


def approve(event):
    number = int(event["number"])
    head = event["pull_request"]["head"]["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", head) or not eligible(pull_request(number), head):
        print("PR is no longer eligible for this run.")
        return

    # Fetch only Git objects. Keep the trusted main worktree and scripts checked out.
    git("fetch", "--no-tags", "origin", f"refs/pull/{number}/head")
    if git("rev-parse", "FETCH_HEAD").decode().strip() != head:
        print("PR head changed. The new revision has its own run.")
        return

    changes = git("diff", "--raw", "--no-abbrev", "--no-renames", "-z", f"HEAD...{head}")
    entries = changes.rstrip(b"\0").split(b"\0")
    if not changes:
        raise InvalidUpload("PR has no file changes")
    pending = {}
    for metadata, raw_path in zip(entries[::2], entries[1::2], strict=True):
        _, mode, _, blob, status = metadata.decode("ascii").split()
        path = raw_path.decode("utf-8")
        if (
            status != "A" or mode != "100644"
            or not path.startswith("update/") or not archive_name(path.removeprefix("update/"))
        ):
            raise InvalidUpload(f"PR may only add regular ZIP files directly under update/: {path!r}")
        if int(git("cat-file", "-s", blob)) > MAX_ZIP:
            raise InvalidUpload(f"ZIP exceeds 50 MiB: {path!r}")
        files, _ = read_archive(io.BytesIO(git("cat-file", "blob", blob)))
        validate_targets(files, pending)

    # Bind the squash merge to the exact head whose Git blobs were validated.
    for attempt in range(6):
        pr = pull_request(number)
        if not eligible(pr, head):
            print("PR changed during validation.")
            return
        if pr["mergeable"] is not None:
            break
        if attempt < 5:
            time.sleep(2)
    if pr["mergeable"] is False:
        raise InvalidUpload("PR has a merge conflict with main")
    subprocess.run(
        [
            "gh", "api", "--method", "PUT", f"repos/{REPOSITORY}/pulls/{number}/merge",
            "-f", f"sha={head}", "-f", "merge_method=squash", "--jq", ".sha",
        ],
        check=True,
    )


def changed_groups(rebuild):
    if rebuild or not INDEX.is_file():
        return {}, set(SUBTITLES.glob("*/*"))

    previous = git("log", "-1", "--format=%H", "--", str(INDEX)).decode().strip()
    if not previous:
        return {}, set(SUBTITLES.glob("*/*"))
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    changed = git("diff", "--name-only", "--no-renames", "-z", previous, "HEAD", "--", str(SUBTITLES))
    groups = set()
    for name in changed.split(b"\0"):
        parts = Path(name.decode("utf-8")).parts
        if len(parts) >= 3:
            groups.add(Path(*parts[:3]))
    return index, groups


def refresh_index(index, groups):
    for group in sorted(groups):
        bucket, prefix = group.parts[1:]
        filenames = []
        if group.is_dir() and not group.is_symlink() and not group.parent.is_symlink():
            filenames = sorted(
                path.name for path in group.iterdir()
                if path.is_file() and not path.is_symlink() and subtitle_path(path.name) == path
            )
        if filenames:
            index.setdefault(bucket, {})[prefix] = filenames
        elif bucket in index:
            index[bucket].pop(prefix, None)
            if not index[bucket]:
                del index[bucket]
    INDEX.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def ingest(rebuild):
    index, groups = changed_groups(rebuild)
    failed = False
    for archive in sorted(INBOX.iterdir()):
        if not archive.name.lower().endswith(".zip"):
            continue
        try:
            if (
                not archive_name(archive.name) or not archive.is_file()
                or archive.is_symlink() or archive.stat().st_size > MAX_ZIP
            ):
                raise InvalidUpload("expected a regular ZIP file of up to 50 MiB")
            files, ignored = read_archive(archive)
            validate_targets(files, {})
        except (InvalidUpload, *ARCHIVE_ERRORS) as error:
            print(f"Rejected {archive.name!r}: {error}", file=sys.stderr)
            failed = True
            continue

        for target, data in files.items():
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            groups.add(target.parent)
        archive.unlink()
        print(f"Imported {archive.name!r}: {len(files)} subtitles, {ignored} unmatched files skipped")

    refresh_index(index, groups)
    return 2 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    approval = commands.add_parser("approve")
    approval.add_argument("event", type=Path)
    ingestion = commands.add_parser("ingest")
    ingestion.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    if args.command == "ingest":
        return ingest(args.rebuild)
    approve(json.loads(args.event.read_text(encoding="utf-8")))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (InvalidUpload, *ARCHIVE_ERRORS) as error:
        sys.exit(f"Upload rejected: {error}")
