"""Đóng gói và khôi phục thư mục checkpoint của job script.

Script tự ghi file trạng thái vào /checkpoint (ghi ra file .tmp rồi đổi tên để
không bị đọc dở), được dùng thư mục con. Worker chạy bằng root ngoài container,
còn container có thể tạo symlink trỏ tới file của máy chủ hoặc của task khác,
nên worker không bao giờ đi theo symlink: trên Linux, mỗi cấp thư mục và mỗi
file được mở bằng tên một cấp, tương đối với descriptor của thư mục cha, có
O_NOFOLLOW. Script không tráo được một cấp thư mục thành symlink, kể cả tráo
đúng lúc worker đang đi.
"""

import hashlib
import io
import os
import shutil
import stat
import tarfile
from pathlib import Path

MAX_BYTES = 64 * 1024 * 1024
MAX_FILES = 1000
MAX_DEPTH = 8  # Số cấp thư mục con tối đa.
MAX_ENTRIES = 5000  # Số mục được duyệt mỗi lần, để script không bắt worker đi một cây quá lớn.
MAX_NAME = 255

DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
# Linux (máy worker) mở theo descriptor của thư mục cha. Windows không có dir_fd, chỉ dùng khi chạy test.
DIR_FD = os.open in os.supports_dir_fd and os.scandir in os.supports_fd
_scandir = os.scandir


class CheckpointTooLarge(ValueError):
    pass


def _file_flags():
    return os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)


def open_regular_file(path):
    """Chỉ mở file thường và giữ nguyên descriptor đã kiểm tra."""
    before = path.lstat()  # Không đi theo symlink, kể cả link trỏ tới file tồn tại.
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("Result must be a regular file, not a symlink or special file")
    descriptor = os.open(path, _file_flags())
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(before, opened):
            raise ValueError("Result file changed before upload")
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def _skipped(name):
    # File .tmp là file script đang ghi dở.
    return name.endswith(".tmp")


def _valid_name(name):
    return (0 < len(name) <= MAX_NAME and name not in (".", "..") and not _skipped(name)
            and not any(char in name for char in "/\\\0"))


def _member_parts(name):
    """Tên trong archive (dạng a/b/c) thành các cấp; None nếu có cấp không hợp lệ hoặc quá sâu."""
    parts = name.split("/")
    if len(parts) > MAX_DEPTH + 1 or not all(_valid_name(part) for part in parts):
        return None
    return parts


def _open_dir(parent, name):
    if DIR_FD:
        return os.open(name, DIR_FLAGS, dir_fd=parent)
    path = os.path.join(parent, name)
    if not stat.S_ISDIR(os.lstat(path).st_mode):
        raise ValueError("Not a directory")
    return path


def _close_dir(handle):
    if DIR_FD:
        os.close(handle)


def _open_file(parent, name):
    if not DIR_FD:
        return open_regular_file(Path(parent) / name)
    descriptor = os.open(name, _file_flags(), dir_fd=parent)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Not a regular file")
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def _walk(root, read):
    """Đi cây checkpoint. Trả về (files, ignored).

    files: [(tên tương đối dạng a/b, inode, kích thước, mtime_ns, nội dung hoặc None)] theo thứ tự tên.
    ignored: symlink, file đặc biệt, tên lạ, thư mục quá sâu; không lưu và không làm hỏng cả bản lưu.
    """
    files, ignored = [], []
    budget = {"entries": 0, "bytes": 0}

    def too_large():
        return CheckpointTooLarge(f"Checkpoint exceeds {MAX_FILES} files or {MAX_BYTES // (1024 * 1024)} MB")

    def visit(directory, prefix, depth):
        with _scandir(directory) as listing:
            entries = sorted(listing, key=lambda entry: entry.name)
        for entry in entries:
            budget["entries"] += 1
            if budget["entries"] > MAX_ENTRIES:
                raise too_large()
            name, relative = entry.name, prefix + entry.name
            if _skipped(name):
                continue
            if not _valid_name(name):
                ignored.append(relative)  # unpack sẽ từ chối cả archive vì tên này.
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    if depth == MAX_DEPTH:
                        ignored.append(relative + "/")
                        continue
                    child = _open_dir(directory, name)
                    try:
                        visit(child, relative + "/", depth + 1)
                    finally:
                        _close_dir(child)
                elif entry.is_file(follow_symlinks=False):
                    with _open_file(directory, name) as handle:
                        info = os.fstat(handle.fileno())
                        data = handle.read(MAX_BYTES + 1 - budget["bytes"]) if read else None
                    budget["bytes"] += len(data) if read else info.st_size
                    if len(files) == MAX_FILES or budget["bytes"] > MAX_BYTES:
                        raise too_large()
                    files.append((relative, info.st_ino, info.st_size, info.st_mtime_ns, data))
                else:
                    ignored.append(relative)
            except FileNotFoundError:
                continue  # Script vừa đổi tên hoặc xóa.
            except CheckpointTooLarge:
                raise
            except (OSError, ValueError):
                # Ví dụ thư mục bị tráo thành symlink giữa lúc đi: O_NOFOLLOW từ chối mở.
                ignored.append(relative)

    top = os.open(root, DIR_FLAGS) if DIR_FD else os.fspath(root)
    try:
        visit(top, "", 0)
    finally:
        _close_dir(top)
    return files, ignored


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def signature(root):
    """Tên, inode, kích thước, thời điểm sửa của các file sẽ được lưu, để biết có gì mới không.

    os.replace luôn cho file mới inode khác, nên vẫn nhận ra thay đổi khi kích thước và
    thời điểm sửa trùng nhau (đồng hồ của hệ thống file thô)."""
    files, _ = _walk(root, read=False)
    return tuple(entry[:4] for entry in files)


def pack(root, archive):
    """Ghi các file thường trong cây root vào archive (tar.gz).

    Trả về (số file, tổng byte, danh sách tên bị bỏ qua). Thư mục rỗng không được lưu.
    """
    files, ignored = _walk(root, read=True)
    # Nén nhẹ: chạy trong lúc container đang chạy, không giành CPU của người dùng.
    with tarfile.open(archive, "w:gz", compresslevel=1) as tar:
        for name, _, _, _, data in files:
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o644
            tar.addfile(info, io.BytesIO(data))
    return len(files), sum(len(entry[4]) for entry in files), ignored


def unpack(archive, root):
    """Giải nén archive vào thư mục root đang trống, trước khi container chạy. Trả về (số file, tổng byte).

    Chỉ nhận file thường, mỗi cấp tên hợp lệ, không trùng, không vừa là file vừa là thư mục;
    sai một mục là bỏ cả archive.
    """
    root = Path(root)
    count, total, files, folders = 0, 0, set(), set()
    with tarfile.open(archive, "r:gz") as tar:
        members = []
        for member in tar:
            parts = _member_parts(member.name) if member.isreg() else None
            parents = {"/".join(parts[:depth]) for depth in range(1, len(parts))} if parts else set()
            if parts is None or member.name in files or member.name in folders or parents & files:
                raise ValueError(f"Invalid checkpoint entry: {member.name!r}")
            files.add(member.name)
            folders |= parents
            total += member.size
            if len(files) > MAX_FILES or total > MAX_BYTES:
                raise CheckpointTooLarge("Checkpoint archive is too large")
            members.append((member, parts))
        for member, parts in members:
            data = tar.extractfile(member).read(MAX_BYTES + 1)
            if len(data) != member.size:
                raise ValueError(f"Truncated checkpoint entry: {member.name!r}")
            # root là thư mục mới tạo và container chưa chạy, nên không có symlink nào trong cây.
            for depth in range(1, len(parts)):
                folder = root.joinpath(*parts[:depth])
                if not folder.is_dir():
                    folder.mkdir()
                    os.chmod(folder, 0o777)  # Script chạy bằng UID 65534 được tạo file trong đó.
            target = root.joinpath(*parts)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
            descriptor = os.open(target, flags, 0o644)
            with os.fdopen(descriptor, "wb") as output:
                output.write(data)
            os.chmod(target, 0o666)  # Script chạy bằng UID 65534 được ghi đè.
            count += 1
    return count, total


def clear(root):
    """Xóa phần đã giải nén dở khi archive hỏng, để task chạy lại từ đầu."""
    with os.scandir(root) as listing:
        for entry in listing:
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path)
            else:
                os.unlink(entry.path)
