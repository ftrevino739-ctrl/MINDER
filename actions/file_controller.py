import hashlib
import os
import shutil
import platform
import stat
from pathlib import Path
from datetime import datetime

try:
    import send2trash
    _SEND2TRASH = True
except ImportError:
    _SEND2TRASH = False

from core.undo import push_undo
from core import confirm as confirm_gate

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"

# Undo keeps a file's previous contents in memory so `write` can be reversed.
# Above this size it does not — a 200 MB log would sit in RAM for the rest of
# the session to protect an edit nobody is going to take back.
_UNDO_CONTENT_LIMIT = 1_000_000


def _file_signature(target: Path):
    """Identity and content fingerprint for a file MINDER may later undo."""
    try:
        before = target.stat()
        if not stat.S_ISREG(before.st_mode):
            return None
        digest = hashlib.sha256()
        with target.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        after = target.stat()
        before_key = (before.st_dev, before.st_ino, before.st_size,
                      before.st_mtime_ns, before.st_mode)
        after_key = (after.st_dev, after.st_ino, after.st_size,
                     after.st_mtime_ns, after.st_mode)
        if before_key != after_key:
            return None
        return (*after_key, digest.hexdigest())
    except (OSError, PermissionError):
        return None


def _tree_signature(target: Path):
    """Fingerprint a copied file/tree so undo never deletes later user edits."""
    try:
        if target.is_file():
            signature = _file_signature(target)
            return ("file", signature) if signature is not None else None
        if not target.is_dir():
            return None

        entries = []

        def _describe(path: Path, relative: str):
            info = path.lstat()
            mode = info.st_mode
            base = (info.st_dev, info.st_ino, info.st_size,
                    info.st_mtime_ns, mode)
            if stat.S_ISLNK(mode):
                return (relative, "symlink", base, os.readlink(path))
            if stat.S_ISDIR(mode):
                return (relative, "directory", base)
            if stat.S_ISREG(mode):
                signature = _file_signature(path)
                return (relative, "file", signature) if signature is not None else None
            return (relative, "other", base)

        root_entry = _describe(target, ".")
        if root_entry is None:
            return None
        entries.append(root_entry)

        for current, directories, files in os.walk(target, topdown=True, followlinks=False):
            directories.sort()
            files.sort()
            current_path = Path(current)
            # Symlinked directories are recorded but are never traversed.
            for name in list(directories):
                child = current_path / name
                entry = _describe(child, child.relative_to(target).as_posix())
                if entry is None:
                    return None
                entries.append(entry)
                if child.is_symlink():
                    directories.remove(name)
            for name in files:
                child = current_path / name
                entry = _describe(child, child.relative_to(target).as_posix())
                if entry is None:
                    return None
                entries.append(entry)

        return ("directory", tuple(entries))
    except OSError:
        return None


def _undo_move(src: Path, dst: Path):
    """Reverse a move without replacing something created at the old path."""
    def _fn():
        if not dst.exists():
            return f"'{dst.name}' is no longer there — nothing moved back."
        if src.exists():
            return (f"Cannot move '{dst.name}' back because '{src.name}' now exists "
                    f"at the original location. Both paths were left untouched.")
        src.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dst), str(src))
        return f"'{src.name}' is back in {src.parent.name}/."
    return _fn


def _undo_create(target: Path, expected_signature=None):
    """Remove a created file only while it still matches the created version.

    Directories are removed only while empty. Later user changes are never
    treated as part of the assistant's original operation."""
    def _fn():
        if not target.exists():
            return f"'{target.name}' is already gone."
        if target.is_dir():
            if any(target.iterdir()):
                return (f"'{target.name}' is not empty any more — "
                        f"leaving it alone rather than deleting your files.")
            target.rmdir()
        else:
            current_signature = _file_signature(target)
            if expected_signature is None or current_signature != expected_signature:
                return (f"'{target.name}' changed after it was created. "
                        f"Leaving it untouched.")
            target.unlink()
        return f"Removed '{target.name}'."
    return _fn


def _undo_write(target: Path, previous: str | None, expected_signature=None):
    """Restore prior text only if the file still matches MINDER's last write."""
    def _fn():
        if not target.exists():
            return f"'{target.name}' is already gone."
        current_signature = _file_signature(target)
        if expected_signature is None or current_signature != expected_signature:
            return (f"'{target.name}' changed after MINDER wrote it. "
                    f"Leaving the newer contents untouched.")
        if previous is None:
            target.unlink()
            return f"Removed '{target.name}' — it did not exist before."
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(previous, encoding="utf-8")
        return f"Restored the previous contents of '{target.name}'."
    return _fn


def _restore_from_trash(original: Path) -> str:
    """Best-effort undelete.

    delete_file uses send2trash, which is the right call: the file lands in the
    Recycle Bin / Trash where the person can also find it themselves. Getting it
    back out again is shell work and only reliable on Windows, where pywin32 is
    already a dependency. Everywhere else this says where the file is instead of
    pretending it failed — the file is not lost either way."""
    if _OS == "Windows":
        try:
            import win32com.client
            shell = win32com.client.Dispatch("Shell.Application")
            bin_folder = shell.NameSpace(10)      # ssfBITBUCKET
            for item in bin_folder.Items():
                if str(bin_folder.GetDetailsOf(item, 1)).strip().lower() == \
                        str(original.parent).strip().lower():
                    if str(item.Name).strip().lower() == original.name.strip().lower():
                        item.InvokeVerb("UNDELETE")
                        return f"'{original.name}' restored from the Recycle Bin."
        except Exception as e:
            print(f"[file] Recycle Bin restore failed: {e}")
    return (f"'{original.name}' is in the Recycle Bin — I could not pull it back "
            f"automatically, but it is there and can be restored by hand.")


_SAFE_ROOTS: list[Path] = [
    Path.home(),
]

def _is_safe_path(target: Path) -> bool:
    """Is the given path inside _SAFE_ROOTS? If not, reject the operation."""
    try:
        resolved = target.resolve()
        return any(
            resolved == root.resolve() or resolved.is_relative_to(root.resolve())
            for root in _SAFE_ROOTS
        )
    except Exception:
        return False

def _get_desktop() -> Path:
    if _OS == "Linux":
        xdg = os.environ.get("XDG_DESKTOP_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Desktop"

def _get_downloads() -> Path:
    if _OS == "Linux":
        xdg = os.environ.get("XDG_DOWNLOAD_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Downloads"

def _get_documents() -> Path:
    if _OS == "Linux":
        xdg = os.environ.get("XDG_DOCUMENTS_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Documents"

def _get_pictures() -> Path:
    if _OS == "Linux":
        xdg = os.environ.get("XDG_PICTURES_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Pictures"

def _get_music() -> Path:
    if _OS == "Linux":
        xdg = os.environ.get("XDG_MUSIC_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Music"

def _get_videos() -> Path:
    if _OS == "Linux":
        xdg = os.environ.get("XDG_VIDEOS_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Videos"


def _resolve_path(raw: str) -> Path:
    shortcuts: dict[str, Path] = {
        "desktop":   _get_desktop(),
        "downloads": _get_downloads(),
        "documents": _get_documents(),
        "pictures":  _get_pictures(),
        "music":     _get_music(),
        "videos":    _get_videos(),
        "home":      Path.home(),
    }
    raw   = raw.strip().strip('"').strip("'")
    lower = raw.lower()
    if lower in shortcuts:
        return shortcuts[lower]

    # "desktop/notes/a.md" and "desktop\notes\a.md" — a shortcut followed by a
    # sub-path.  Without this branch the whole string falls through to the
    # relative-path return below and is resolved against the process CWD instead
    # of the real Desktop: an "Access denied" when the project lives outside the
    # home directory, or — worse — a silent write into a stray "desktop" folder
    # inside the project when it lives inside it.
    head, sep, rest = raw.replace("\\", "/").partition("/")
    if sep and head.lower() in shortcuts:
        rest = rest.strip("/")
        return shortcuts[head.lower()] / rest if rest else shortcuts[head.lower()]

    return Path(raw).expanduser()

def _format_size(b: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"

def _safe_trash(target: Path) -> str:

    if not _SEND2TRASH:
        return (
            "send2trash is not installed. "
            "Run: pip install send2trash — "
            "Permanent deletion is disabled for safety."
        )
    send2trash.send2trash(str(target))
    return f"Moved to Trash: {target.name}"


def list_files(path: str = "desktop", show_hidden: bool = False) -> str:
    try:
        target = _resolve_path(path)
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        if not target.exists():
            return f"Path not found: {target}"
        if not target.is_dir():
            return f"Not a directory: {target}"

        items = []
        for item in sorted(target.iterdir()):
            if not show_hidden and item.name.startswith("."):
                continue
            if item.is_dir():
                items.append(f"📁 {item.name}/")
            else:
                size = _format_size(item.stat().st_size)
                items.append(f"📄 {item.name} ({size})")

        if not items:
            return f"Directory is empty: {target.name}/"

        return f"Contents of {target.name}/ ({len(items)} items):\n" + "\n".join(items)

    except PermissionError:
        return f"Permission denied: {path}"
    except Exception as e:
        return f"Error listing files: {e}"


def create_file(path: str, name: str = "", content: str = "") -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        if target.exists():
            return (f"'{target.name}' already exists. Nothing was changed; "
                    f"use the write action to replace its contents.")
        target.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation closes the check/write race: never truncate a file
        # that appeared after the exists() check.
        with target.open("x", encoding="utf-8") as handle:
            handle.write(content)
        push_undo(f"created {target.name}",
                  _undo_create(target, _file_signature(target)))
        return f"File created: {target.name}"
    except FileExistsError:
        return f"'{name or path}' already exists. Nothing was changed."
    except Exception as e:
        return f"Could not create file: {e}"


def create_folder(path: str, name: str = "") -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        already = target.exists()
        target.mkdir(parents=True, exist_ok=True)
        # Only offer to undo a folder we actually made. "mkdir -p" on something
        # that was already there is not a change, and undoing it would delete a
        # directory the user has had for years.
        if not already:
            push_undo(f"created folder {target.name}", _undo_create(target))
        return f"Folder created: {target.name}"
    except Exception as e:
        return f"Could not create folder: {e}"


def _delete_approved_target(target: Path, original: Path, expected_signature) -> str:
    """Move the reviewed target to Trash only if it is still the same file/tree."""
    try:
        if target.is_symlink() or not _is_safe_path(target):
            return "Delete refused: the target became a symbolic link or left the safe home folder."
        if not target.exists() or target.resolve() != original:
            return "Delete refused: the target changed or disappeared while approval was pending."
        if _tree_signature(target) != expected_signature:
            return "Delete refused: the file or folder changed while approval was pending."
        result = _safe_trash(target)
        if result.startswith("Moved to Trash"):
            push_undo(
                f"deleted {original.name}",
                lambda p=original: _restore_from_trash(p),
            )
        return result
    except Exception as exc:
        return f"Could not move to Trash: {exc}"


def delete_file(path: str, name: str = "") -> str:
    try:
        base = _resolve_path(path)
        target = (base / name) if name else base
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        if target.is_symlink():
            return f"Access denied: symbolic links cannot be deleted by MINDER: {target}"
        if not target.exists():
            return f"Not found: {target.name}"

        # Protect the user's well-known home folders even if their contents
        # are empty. A delete request must name an item inside them instead.
        protected = {
            _get_desktop(), _get_downloads(), _get_documents(),
            _get_pictures(), _get_music(), _get_videos(), Path.home()
        }
        if target.resolve() in {p.resolve() for p in protected}:
            return f"Protected directory, cannot delete: {target.name}"

        original = target.resolve()
        expected_signature = _tree_signature(target)
        if expected_signature is None:
            return f"Delete refused: could not safely fingerprint {target.name}."

        if target.is_dir():
            entry_count = max(0, len(expected_signature[1]) - 1)
            byte_count = sum(
                entry[2][2]
                for entry in expected_signature[1]
                if len(entry) > 2 and entry[1] == "file" and entry[2] is not None
            )
            target_kind = f"folder containing {entry_count} item(s)"
        else:
            byte_count = target.stat().st_size
            target_kind = "file"

        detail = (
            f"Exact path: {original}\n"
            f"Target type: {target_kind}\n"
            f"Approximate data size: {byte_count:,} bytes\n\n"
            "MINDER will move this exact target to the operating system's Trash / "
            "Recycle Bin, not permanently erase it. The target will be fingerprinted "
            "again after approval; if it changed, the operation will be refused."
        )
        if len(detail) > confirm_gate.MAX_DETAIL_CHARS:
            return (
                "Delete refused: the complete target details could not be displayed "
                "for approval."
            )

        return confirm_gate.request(
            key=f"file-delete:{original}",
            title=f"Approve moving to Trash: {original.name}",
            detail=detail,
            run=lambda: _delete_approved_target(
                target, original, expected_signature,
            ),
        )

    except PermissionError:
        return f"Permission denied: {path}"
    except Exception as e:
        return f"Could not delete: {e}"


def move_file(path: str, name: str = "", destination: str = "") -> str:
    try:
        base   = _resolve_path(path)
        src    = (base / name) if name else base
        dst    = _resolve_path(destination) if destination else None

        if not src.exists():
            return f"Source not found: {src.name}"
        if dst is None:
            return "No destination specified."
        if not _is_safe_path(src):
            return f"Access denied (source): {src}"
        if not _is_safe_path(dst):
            return f"Access denied (destination): {dst}"

        if dst.is_dir():
            dst = dst / src.name

        if src.resolve() == dst.resolve():
            return "Source and destination are the same path. Nothing was moved."
        if dst.exists():
            return (f"Destination already exists: {dst.name}. "
                    f"Nothing was moved; choose a different destination.")
        dst.parent.mkdir(parents=True, exist_ok=True)
        origin = src.resolve()
        shutil.move(str(src), str(dst))
        push_undo(f"moved {origin.name} to {dst.parent.name}/",
                  _undo_move(origin, dst.resolve()))
        return f"Moved: {src.name} → {dst.parent.name}/"

    except Exception as e:
        return f"Could not move: {e}"


def copy_file(path: str, name: str = "", destination: str = "") -> str:
    try:
        base = _resolve_path(path)
        src  = (base / name) if name else base
        dst  = _resolve_path(destination) if destination else None

        if not src.exists():
            return f"Source not found: {src.name}"
        if dst is None:
            return "No destination specified."
        if not _is_safe_path(src):
            return f"Access denied (source): {src}"
        if not _is_safe_path(dst):
            return f"Access denied (destination): {dst}"

        if dst.is_dir():
            dst = dst / src.name

        if src.resolve() == dst.resolve():
            return "Source and destination are the same path. Nothing was copied."
        if dst.exists():
            return (f"Destination already exists: {dst.name}. "
                    f"Nothing was copied; choose a different destination.")
        dst.parent.mkdir(parents=True, exist_ok=True)

        if src.is_dir():
            shutil.copytree(str(src), str(dst))
        else:
            # "xb" prevents copy2 from silently truncating a destination that
            # appears between the exists() check and the copy.
            destination_created = False
            try:
                with src.open("rb") as source_handle:
                    with dst.open("xb") as target_handle:
                        destination_created = True
                        shutil.copyfileobj(source_handle, target_handle)
                shutil.copystat(src, dst)
            except Exception:
                # Remove only a partial file created by this operation. If the
                # exclusive open failed because another process won the race,
                # that process's file must be left untouched.
                if destination_created:
                    try:
                        dst.unlink(missing_ok=True)
                    except Exception:
                        pass
                raise

        # Undo may remove only the exact copy MINDER made. If the user has
        # edited, added, replaced or removed anything inside it, keep it.
        _copy = dst.resolve()
        expected_copy_signature = _tree_signature(_copy)
        def _undo_copy():
            if not _copy.exists():
                return f"The copy '{_copy.name}' is already gone."
            current_signature = _tree_signature(_copy)
            if (expected_copy_signature is None
                    or current_signature != expected_copy_signature):
                return (f"The copy '{_copy.name}' changed after it was created. "
                        f"Leaving it untouched.")
            if _copy.is_dir():
                shutil.rmtree(_copy)
            else:
                _copy.unlink()
            return f"Removed the unchanged copy in {_copy.parent.name}/."
        push_undo(f"copied {src.name} to {dst.parent.name}/", _undo_copy)

        return f"Copied: {src.name} → {dst.parent.name}/"

    except Exception as e:
        return f"Could not copy: {e}"


def rename_file(path: str, name: str = "", new_name: str = "") -> str:
    try:
        base     = _resolve_path(path)
        target   = (base / name) if name else base
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        if not target.exists():
            return f"Not found: {target.name}"
        if not new_name:
            return "No new name provided."

        new_path = target.parent / new_name
        if new_path.exists():
            return f"A file named '{new_name}' already exists here."

        old_path = target.resolve()
        target.rename(new_path)
        push_undo(f"renamed {old_path.name} to {new_name}",
                  _undo_move(old_path, new_path.resolve()))
        return f"Renamed: {target.name} → {new_name}"

    except Exception as e:
        return f"Could not rename: {e}"


def read_file(path: str, name: str = "", max_chars: int = 4000) -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        if not target.exists():
            return f"File not found: {target.name}"
        if not target.is_file():
            return f"Not a file: {target.name}"

        content = target.read_text(encoding="utf-8", errors="ignore")
        if len(content) > max_chars:
            content = content[:max_chars] + f"\n\n[Truncated — {len(content)} total chars]"
        return content

    except Exception as e:
        return f"Could not read file: {e}"


def write_file(path: str, name: str = "", content: str = "",
               append: bool = False) -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        target.parent.mkdir(parents=True, exist_ok=True)

        existed = target.exists()
        if existed and not target.is_file():
            return f"Not a regular file: {target.name}"

        # Preserve a reliable undo snapshot only for small, valid UTF-8 files.
        # Decoding with errors='ignore' is unsafe here: it silently discards
        # bytes and can turn an apparent undo into permanent corruption.
        previous: str | None = None
        undoable = True
        original_stat = None
        if existed:
            try:
                original_stat = target.stat()
                if original_stat.st_size > _UNDO_CONTENT_LIMIT:
                    undoable = False
                else:
                    previous = target.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                undoable = False

        def _perform_write() -> str:
            # Recheck the target after a confirmation delay. Do not overwrite a
            # file that changed while the user was deciding, or one that was
            # created at this path after the original request.
            if existed:
                try:
                    current_stat = target.stat()
                except OSError:
                    return f"'{target.name}' changed or disappeared. Nothing was written."
                if (original_stat is None
                        or current_stat.st_size != original_stat.st_size
                        or current_stat.st_mtime_ns != original_stat.st_mtime_ns):
                    return (f"'{target.name}' changed while awaiting the operation. "
                            f"Nothing was written; review it and try again.")
            elif target.exists():
                return (f"A file named '{target.name}' appeared at this location. "
                        f"Nothing was written.")

            # Use exclusive creation for a new path. "w" could truncate a
            # file created by another process just after the exists() check.
            mode = ("a" if append else "w") if existed else "x"
            try:
                with target.open(mode, encoding="utf-8") as handle:
                    handle.write(content)
            except FileExistsError:
                return (f"A file named '{target.name}' appeared at this location. "
                        f"Nothing was written.")

            action = "Appended to" if append else "Written to"
            if undoable:
                expected_signature = _file_signature(target)
                if expected_signature is not None:
                    push_undo(
                        f"wrote to {target.name}",
                        _undo_write(target, previous, expected_signature),
                    )
                    return f"{action}: {target.name}"
                return (f"{action}: {target.name}. "
                        f"(I could not verify a safe undo snapshot, so this change "
                        f"cannot be undone.)")
            return (f"{action}: {target.name}. "
                    f"(This file could not be snapshotted safely, so the change "
                    f"cannot be undone.)")

        if existed and not undoable:
            reason = ("larger than the undo snapshot limit"
                      if original_stat and original_stat.st_size > _UNDO_CONTENT_LIMIT
                      else "not readable as UTF-8 text")
            return confirm_gate.request(
                key=f"file-write:{target.resolve()}",
                title=f"Overwrite {target.name} without undo",
                detail=(f"The existing file is {reason}. Replacing it cannot be "
                        f"undone. No changes have been made yet."),
                run=_perform_write,
            )

        return _perform_write()
    except Exception as e:
        return f"Could not write file: {e}"
def find_files(name: str = "", extension: str = "",
               path: str = "home", max_results: int = 20) -> str:
    try:
        search_path = _resolve_path(path)
        if not _is_safe_path(search_path):
            return f"Access denied: {search_path}"
        if not search_path.exists():
            return f"Search path not found: {path}"

        results    = []
        dir_count  = 0
        max_dirs   = 500  # performance + safety limit

        for item in search_path.rglob("*"):
            if item.is_dir():
                dir_count += 1
                if dir_count > max_dirs:
                    break
                continue
            if not item.is_file():
                continue
            if extension and item.suffix.lower() != extension.lower():
                continue
            if name and name.lower() not in item.name.lower():
                continue
            size = _format_size(item.stat().st_size)
            results.append(f"📄 {item.name} ({size}) — {item.parent}")
            if len(results) >= max_results:
                break

        if not results:
            query = name or extension or "files"
            return f"No {query} found in {search_path.name}/"

        return f"Found {len(results)} file(s):\n" + "\n".join(results)

    except Exception as e:
        return f"Search error: {e}"


def get_largest_files(path: str = "downloads", count: int = 10) -> str:
    count = min(count, 50)  # maksimum 50
    try:
        search_path = _resolve_path(path)
        if not _is_safe_path(search_path):
            return f"Access denied: {search_path}"
        if not search_path.exists():
            return f"Path not found: {path}"

        files = []
        for item in search_path.rglob("*"):
            if item.is_file():
                try:
                    files.append((item.stat().st_size, item))
                except Exception:
                    continue

        files.sort(reverse=True)
        top = files[:count]

        if not top:
            return "No files found."

        lines = [f"Top {len(top)} largest files in {search_path.name}/:"]
        for size, f in top:
            lines.append(f"  {_format_size(size):>10}  {f.name}  ({f.parent})")

        return "\n".join(lines)

    except Exception as e:
        return f"Error: {e}"


def get_disk_usage(path: str = "home") -> str:
    try:
        target = _resolve_path(path)
        usage  = shutil.disk_usage(target)
        pct    = usage.used / usage.total * 100
        return (
            f"Disk usage ({target}):\n"
            f"  Total : {_format_size(usage.total)}\n"
            f"  Used  : {_format_size(usage.used)} ({pct:.1f}%)\n"
            f"  Free  : {_format_size(usage.free)}"
        )
    except Exception as e:
        return f"Could not get disk usage: {e}"


def organize_desktop() -> str:
    type_map = {
        "Images":    {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".ico", ".heic"},
        "Documents": {".pdf", ".doc", ".docx", ".txt", ".xls", ".xlsx",
                      ".ppt", ".pptx", ".csv", ".odt", ".ods", ".odp"},
        "Videos":    {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v"},
        "Music":     {".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a"},
        "Archives":  {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
        "Code":      {".py", ".js", ".ts", ".html", ".css", ".json", ".xml",
                      ".cpp", ".java", ".cs", ".go", ".rs", ".sh"},
    }

    desktop = _get_desktop()
    moved, skipped = [], []
    journal: list[tuple[Path, Path]] = []   # (where it was, where it went)

    try:
        for item in desktop.iterdir():
            # Leave folders, hidden files and organize-folders untouched
            if item.is_dir() or item.name.startswith("."):
                continue
            if item.name in {k for k in type_map}:
                continue

            ext        = item.suffix.lower()
            target_dir = desktop / "Others"
            for folder, exts in type_map.items():
                if ext in exts:
                    target_dir = desktop / folder
                    break

            target_dir.mkdir(exist_ok=True)
            new_path = target_dir / item.name

            if new_path.exists():
                skipped.append(item.name)
                continue

            origin = item.resolve()
            shutil.move(str(item), str(new_path))
            journal.append((origin, new_path.resolve()))
            moved.append(f"{item.name} → {target_dir.name}/")

        # One command, dozens of moves — so one undo that reverses all of them.
        # Without this, "organize my desktop" is the single least reversible
        # thing the assistant can do to a person's files, and it was completely
        # ungated.
        if journal:
            def _undo_organize(entries=tuple(journal)):
                restored = 0
                for origin, moved_to in entries:
                    try:
                        if moved_to.exists() and not origin.exists():
                            origin.parent.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(moved_to), str(origin))
                            restored += 1
                    except Exception as e:
                        print(f"[file] undo organize: {moved_to.name}: {e}")
                # Clear away the folders we created, but only while they are
                # empty — anything the user put in since stays.
                for folder in {m.parent for _o, m in entries}:
                    try:
                        if folder.exists() and folder.is_dir() and not any(folder.iterdir()):
                            folder.rmdir()
                    except Exception:
                        pass
                return f"{restored} file(s) put back on the desktop."
            push_undo(f"organized the desktop ({len(journal)} files)", _undo_organize)

        result = f"Desktop organized: {len(moved)} files moved."
        if moved:
            preview = moved[:8]
            result += "\n" + "\n".join(preview)
            if len(moved) > 8:
                result += f"\n... and {len(moved) - 8} more."
        if skipped:
            result += f"\n{len(skipped)} file(s) skipped (name conflict)."
        return result

    except Exception as e:
        return f"Could not organize desktop: {e}"


def get_file_info(path: str, name: str = "") -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        if not target.exists():
            return f"Not found: {target.name}"

        stat = target.stat()
        info = {
            "Name":      target.name,
            "Type":      "Folder" if target.is_dir() else "File",
            "Size":      _format_size(stat.st_size),
            "Location":  str(target.parent),
            "Created":   datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M"),
            "Modified":  datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            "Extension": target.suffix or "—",
        }
        return "\n".join(f"  {k}: {v}" for k, v in info.items())

    except Exception as e:
        return f"Could not get file info: {e}"

def file_controller(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    action = params.get("action", "").lower().strip()
    path   = params.get("path", "desktop")
    name   = params.get("name", "")

    if player:
        print(f"[File] {action} {name or path}")  # terminal, not the HUD

    try:
        if action == "list":
            return list_files(path)

        elif action == "create_file":
            return create_file(path, name=name, content=params.get("content", ""))

        elif action == "create_folder":
            return create_folder(path, name=name)

        elif action == "delete":
            return delete_file(path, name=name)

        elif action == "move":
            return move_file(path, name=name, destination=params.get("destination", ""))

        elif action == "copy":
            return copy_file(path, name=name, destination=params.get("destination", ""))

        elif action == "rename":
            return rename_file(path, name=name, new_name=params.get("new_name", ""))

        elif action == "read":
            return read_file(path, name=name)

        elif action == "write":
            return write_file(
                path, name=name,
                content=params.get("content", ""),
                append=params.get("append", False)
            )

        elif action == "find":
            return find_files(
                name=name or params.get("name", ""),
                extension=params.get("extension", ""),
                path=path,
                max_results=min(int(params.get("max_results", 20)), 50),
            )

        elif action == "largest":
            return get_largest_files(
                path=path,
                count=int(params.get("count", 10)),
            )

        elif action == "disk_usage":
            return get_disk_usage(path)

        elif action == "organize_desktop":
            return organize_desktop()

        elif action == "info":
            return get_file_info(path, name=name)

        else:
            return f"Unknown action: '{action}'"

    except Exception as e:
        return f"File controller error ({action}): {e}"


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "file_controller",
    "description": "Manages files and folders: list, create, delete, move, copy, rename, read, write, find, disk usage.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info"
            },
            "path": {
                "type": "STRING",
                "description": "File/folder path or shortcut: desktop, downloads, documents, home"
            },
            "destination": {
                "type": "STRING",
                "description": "Destination path for move/copy"
            },
            "new_name": {
                "type": "STRING",
                "description": "New name for rename"
            },
            "content": {
                "type": "STRING",
                "description": "Content for create_file/write"
            },
            "name": {
                "type": "STRING",
                "description": "File name to search for"
            },
            "extension": {
                "type": "STRING",
                "description": "File extension to search (e.g. .pdf)"
            },
            "count": {
                "type": "INTEGER",
                "description": "Number of results for largest"
            }
        },
        "required": [
            "action"
        ]
    },
    "handler": file_controller,
}
