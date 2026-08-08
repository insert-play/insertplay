namespace InsertPlay.Cli.Application.Abstractions;

public interface IShortcutIdentityStore
{
    bool TryGetOrCreate(
        string sdRoot,
        string appName,
        string? manifestGuid,
        out string shortcutGuid,
        out string error);

    /// <summary>
    /// Read-only lookup of an existing shortcut identity GUID, without creating or touching the file.
    /// Returns false with an empty error when no identity file exists at all (not an error condition),
    /// and false with a non-empty error when a file exists but is invalid.
    /// </summary>
    bool TryRead(string sdRoot, out string shortcutGuid, out string error);
}
