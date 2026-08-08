using InsertPlay.Cli.Application.Abstractions;
using InsertPlay.Cli.Domain.Steam;
using InsertPlay.Cli.Infrastructure.Steam.Vdf;

namespace InsertPlay.Cli.Infrastructure.Steam;

public sealed class VdfShortcutRepository(IFileSystem fileSystem) : IShortcutRepository
{
    public IReadOnlyList<SteamShortcutEntry> GetAll(string steamPath, string userId)
    {
        var vdfPath = GetOrCreateShortcutsVdfPath(steamPath, userId);
        var doc = VdfBinaryDocument.Load(vdfPath);
        return doc.Shortcuts;
    }

    public bool AddOrUpdate(string steamPath, string userId, SteamShortcutEntry entry, out string message)
    {
        var vdfPath = GetOrCreateShortcutsVdfPath(steamPath, userId);
        var backup = vdfPath + ".bak";
        BackupIfExists(vdfPath, backup);

        try
        {
            var doc = VdfBinaryDocument.Load(vdfPath);
            var appId = AppIdCalculator.Calculate(entry.Exe, entry.AppName);

            var existing = doc.Shortcuts.FirstOrDefault(s =>
                s.AppName.Equals(entry.AppName, StringComparison.OrdinalIgnoreCase));

            if (existing is null)
            {
                existing = new SteamShortcutEntry();
                doc.Shortcuts.Add(existing);
            }

            existing.AppName = entry.AppName;
            existing.Exe = entry.Exe;
            existing.StartDir = entry.StartDir;
            existing.LaunchOptions = entry.LaunchOptions;
            existing.Icon = entry.Icon;
            existing.IsHidden = entry.IsHidden;
            existing.AppId = appId;

            doc.Save(vdfPath);
            message = $"Shortcut saved with AppId {appId}.";
            DeleteBackup(backup);
            return true;
        }
        catch (Exception ex)
        {
            TryRollback(backup, vdfPath);
            message = $"Failed to write shortcut: {ex.Message}";
            return false;
        }
    }

    public bool Remove(string steamPath, string userId, string appName, out string message)
    {
        var vdfPath = GetOrCreateShortcutsVdfPath(steamPath, userId);
        var backup = vdfPath + ".bak";
        BackupIfExists(vdfPath, backup);

        try
        {
            var doc = VdfBinaryDocument.Load(vdfPath);
            var before = doc.Shortcuts.Count;
            doc.Shortcuts.RemoveAll(s => s.AppName.Equals(appName, StringComparison.OrdinalIgnoreCase));

            if (doc.Shortcuts.Count == before)
            {
                message = "Shortcut not found.";
                DeleteBackup(backup);
                return false;
            }

            doc.Save(vdfPath);
            DeleteBackup(backup);
            message = "Shortcut removed.";
            return true;
        }
        catch (Exception ex)
        {
            TryRollback(backup, vdfPath);
            message = $"Failed to remove shortcut: {ex.Message}";
            return false;
        }
    }

    private string GetOrCreateShortcutsVdfPath(string steamRoot, string userId)
    {
        var existing = FindShortcutsVdf(steamRoot, userId);
        if (existing is not null)
            return existing;

        var dir = Path.Combine(steamRoot, "userdata", userId, "config");
        fileSystem.CreateDirectory(dir);
        return Path.Combine(dir, "shortcuts.vdf");
    }

    private string? FindShortcutsVdf(string steamRoot, string userId)
    {
        var path = Path.Combine(steamRoot, "userdata", userId, "config", "shortcuts.vdf");
        return fileSystem.FileExists(path) ? path : null;
    }

    private void BackupIfExists(string source, string backup)
    {
        if (fileSystem.FileExists(source))
            fileSystem.CopyFile(source, backup, overwrite: true);
    }

    private void TryRollback(string backup, string target)
    {
        if (fileSystem.FileExists(backup))
            fileSystem.CopyFile(backup, target, overwrite: true);
    }

    private void DeleteBackup(string backup)
    {
        if (fileSystem.FileExists(backup))
            fileSystem.DeleteFile(backup);
    }
}
