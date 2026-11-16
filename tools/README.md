# tools/

Helper tools for the machine you develop on (Windows). They are not part of the plugin and are not shipped in the release zip. The tools that talk to the Steam Deck / Steam Machine live in [`../scripts/`](../scripts) (see [`../docs/development.md`](../docs/development.md)).

## Configuration

Settings that change from one machine to another live in `tools/.env`, which is **not versioned** (it is in `.gitignore`). The versioned template is [`.env.example`](.env.example).

```powershell
Copy-Item tools\.env.example tools\.env
notepad tools\.env
```

Each variable is explained in the template, with the PowerShell command that finds its value. Lines are `KEY=VALUE`; `#` starts a comment. A command-line parameter always overrides the `.env` value.

## wsl-disk.ps1: use a Linux-formatted USB disk from Windows

Mounts a USB disk formatted with a Linux filesystem (ext4, btrfs, ...) through WSL2 and gives it its **own drive letter**, so Explorer shows only that disk. It was written to evaluate ext4 for the Remote Library server's data disk (see [`../docs/remote-library.md`](../docs/remote-library.md)): Windows cannot read ext4 natively, and this makes plugging the disk into the PC for a bulk copy practical.

```powershell
.\tools\wsl-disk.ps1 mount      # one UAC prompt; the disk appears as a drive letter (default: first free from X)
.\tools\wsl-disk.ps1 status     # disk found? letter? keep-alive running? real free space
.\tools\wsl-disk.ps1 unmount    # one UAC prompt; afterwards it is safe to unplug
```

Run it from a normal (non-admin) PowerShell. Add `-ReadWrite` (or `WSL_DISK_READWRITE=true`) to mount read-write; the default is read-only.

### Requirements

- Windows 11 with WSL2 and one Linux distro installed (`wsl -l -v`).
- **Developer Mode** on (Settings > System > For developers), so the symlink can be created without admin.
- The disk's filesystem must be supported by the WSL kernel (ext4 is; check others with `wsl -- cat /proc/filesystems`).
- The disk must be an **external USB data disk**. The script refuses internal and system disks.

### How it works

1. `wsl --mount \\.\PHYSICALDRIVE<n> --partition <p> --type <fs> --name <name>` attaches and mounts the partition inside WSL at `/mnt/wsl/<name>`. This is the only step that needs admin, so the script re-launches itself elevated just for it. The disk is found by serial number, because the drive number changes between plugs.
2. A hidden `sleep infinity` keeps the distro alive. Without it WSL idles the distro out and the mount disappears.
3. A directory symlink `%LOCALAPPDATA%\wsl-disk\<name>` points to `\\wsl.localhost\<distro>\mnt\wsl\<name>`, and `subst` turns that link into a drive letter.

Why the symlink and `subst`: `net use` only accepts the distro **root** (`\\wsl.localhost\<distro>`, error 64 on subfolders). Mapping that root would put the whole distro (and its virtual disk) behind the letter, with its free space instead of the disk's. This way the letter contains only the disk.

### Things to know

- **The letter is per session.** `subst` letters disappear on reboot or logoff. Run `mount` again.
- **The drive icon looks "disconnected".** Cosmetic: Explorer treats the letter as a network drive because the link target is a `\\wsl.localhost` path. The contents open normally.
- **Free space in Explorer is the real disk's**; `status` and `mount` also print `df` from inside Linux.
- **Safety:** the script only removes the letter and the link it created itself, never another drive's letter, and refuses to delete a real folder that happens to have the link's name. It never formats or writes to the disk.
- Apps running as administrator do not see `subst` letters (Windows behaviour).
- Always unmount before unplugging. If `unmount` fails, close Explorer windows and programs that have the drive open and try again.
- Do not look for the disk at `/mnt/<name>` (not `/mnt/wsl/<name>`): a plain `mount` run from a `wsl` command lands in a mount namespace that `\\wsl.localhost` does not see, so the folder looks empty.

### Status

Exercised end to end: `mount`, `status` and `unmount` on a btrfs SSD (read-only), and ext4 read-write on 1 TB and 2 TB USB HDDs, including formatting (`mkfs.ext4` run inside WSL on the attached disk) and a ~180 GiB `rsync` from inside WSL. See [server/README.md](../server/README.md#moving-the-library-to-another-disk-ntfs-to-ext4) for the copy and speed notes.

- **Writing through the letter is slow**: `\\wsl.localhost` (9P) costs ~6 ms per file and has no flush. For big copies run `rsync` inside WSL against `/mnt/wsl/<name>`; use the letter to browse.
- **If a disk shows I/O retries, try a USB port of the CPU's controller** (usually the rear ones) instead of a chipset port.
- **Several disks**: change the serial in `.env`; the script refuses anything that is not a USB disk or that holds Windows.
- **Known bug**: `unmount` can print "Safe to unplug" even when detaching failed. Run `status` and check the disk is gone before pulling the cable.
