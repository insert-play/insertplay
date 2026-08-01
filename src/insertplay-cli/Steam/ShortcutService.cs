using InsertPlay.Cli.Models;

namespace InsertPlay.Cli.Steam;

public sealed class ShortcutService
{
    public IReadOnlyList<VdfShortcutEntry> List(string steamPath, string userId)
    {
        var vdfPath = SteamPaths.GetOrCreateShortcutsVdfPath(steamPath, userId);
        var doc = VdfBinaryDocument.Load(vdfPath);
        return doc.Shortcuts;
    }

    public bool AddOrUpdate(
        string steamPath,
        string userId,
        string appName,
        string exe,
        string startDir,
        string launchOptions,
        string icon,
        out string message)
        => AddOrUpdate(
            steamPath,
            userId,
            appName,
            exe,
            startDir,
            launchOptions,
            icon,
            isHidden: false,
            out message);

    public bool AddOrUpdate(
        string steamPath,
        string userId,
        string appName,
        string exe,
        string startDir,
        string launchOptions,
        string icon,
        bool isHidden,
        out string message)
    {
        var vdfPath = SteamPaths.GetOrCreateShortcutsVdfPath(steamPath, userId);
        var backup = vdfPath + ".bak";
        BackupIfExists(vdfPath, backup);

        try
        {
            var doc = VdfBinaryDocument.Load(vdfPath);
            var appId = AppIdCalculator.Calculate(exe, appName);

            var existing = doc.Shortcuts.FirstOrDefault(s =>
                s.AppName.Equals(appName, StringComparison.OrdinalIgnoreCase));

            if (existing is null)
            {
                existing = new VdfShortcutEntry();
                doc.Shortcuts.Add(existing);
            }

            existing.AppName = appName;
            existing.Exe = exe;
            existing.StartDir = startDir;
            existing.LaunchOptions = launchOptions;
            existing.Icon = icon;
            existing.IsHidden = isHidden;
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
        var vdfPath = SteamPaths.GetOrCreateShortcutsVdfPath(steamPath, userId);
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

    private static void BackupIfExists(string source, string backup)
    {
        if (File.Exists(source))
            File.Copy(source, backup, overwrite: true);
    }

    private static void TryRollback(string backup, string target)
    {
        if (File.Exists(backup))
            File.Copy(backup, target, overwrite: true);
    }

    private static void DeleteBackup(string backup)
    {
        if (File.Exists(backup))
            File.Delete(backup);
    }
}
