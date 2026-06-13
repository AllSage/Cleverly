# Encrypted Docker Data Root

Cleverly sealed mode stores app data and models in Docker named volumes. On
Docker Desktop for Windows, those volumes live inside Docker Desktop's Linux
data disk, usually:

```text
C:\Users\<user>\AppData\Local\Docker\wsl\disk\docker_data.vhdx
```

Encrypt the Windows volume that holds that VHDX to protect Cleverly data at
rest. On Windows 10/11 Pro, the practical path is BitLocker.

This protects against offline theft of the disk or copied VHDX. It does not
protect against a logged-in Windows administrator, a user with Docker Desktop
access, malware running in your session, or the app while the machine is
unlocked.

## Windows Check

Run from an Administrator PowerShell session:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows-docker-data-root-bitlocker.ps1
```

The script finds Docker Desktop's data VHDX and reports the BitLocker status of
the Windows volume that contains it. Some Windows policies deny BitLocker status
to non-admin users. Use this stricter check in setup scripts:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows-docker-data-root-bitlocker.ps1 -RequireEncrypted
```

## Windows Enable

Run PowerShell as Administrator and pass a recovery-key path on a removable
drive or another secure location:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows-docker-data-root-bitlocker.ps1 -Enable -RecoveryKeyPath E:\Cleverly-BitLocker-RecoveryKey.txt
```

The script:

- Finds Docker Desktop's data VHDX.
- Finds the Windows volume that stores it.
- Adds a BitLocker recovery password protector.
- Writes the recovery password to the path you provided.
- Starts BitLocker with used-space-only `XtsAes256` encryption.

Automated enable is limited to the Windows OS drive, which is where Docker
Desktop stores data by default. If you moved Docker Desktop data to another
drive, use the Windows BitLocker UI to protect that drive, then rerun the script
without `-Enable` to verify it.

Keep the recovery key separate from the protected computer. Losing it can make
the disk unrecoverable after hardware, TPM, firmware, or boot changes.

## Current Windows Host

On this host, Docker Desktop data was detected at:

```text
C:\Users\allsage\AppData\Local\Docker\wsl\disk\docker_data.vhdx
```

That file is on `C:`. Encrypting `C:` with BitLocker encrypts Docker's sealed
Cleverly volumes at rest.

## Linux Equivalent

On Linux, use a LUKS-encrypted filesystem for Docker's `data-root`.

High-level flow:

```bash
sudo systemctl stop docker
sudo cryptsetup luksFormat /dev/<disk-or-partition>
sudo cryptsetup open /dev/<disk-or-partition> docker_crypt
sudo mkfs.ext4 /dev/mapper/docker_crypt
sudo mkdir -p /var/lib/docker-encrypted
sudo mount /dev/mapper/docker_crypt /var/lib/docker-encrypted
sudo rsync -aHAX /var/lib/docker/ /var/lib/docker-encrypted/
```

Then set Docker's data root in `/etc/docker/daemon.json`:

```json
{
  "data-root": "/var/lib/docker-encrypted"
}
```

Start Docker again:

```bash
sudo systemctl start docker
docker info | grep "Docker Root Dir"
```

Do not delete the old `/var/lib/docker` until containers, images, and Cleverly
volumes have been verified from the encrypted data root.
