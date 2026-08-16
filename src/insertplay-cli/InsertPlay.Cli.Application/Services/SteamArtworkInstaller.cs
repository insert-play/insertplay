using InsertPlay.Cli.Application.Abstractions;

namespace InsertPlay.Cli.Application.Services;

/// <summary>
/// Copies shortcut artwork into Steam's grid folder using Steam's file-naming convention.
/// Shared by the "SD CARD" bootstrap shortcut (setup) and the per-game runtime shortcut (install-shortcut),
/// which previously duplicated this copy logic.
/// </summary>
public sealed class SteamArtworkInstaller(IFileSystem fileSystem)
{
    public int Apply(string steamPath, string userId, uint appId, SteamArtworkPaths artwork)
    {
        var gridDir = Path.Combine(steamPath, "userdata", userId, "config", "grid");
        fileSystem.CreateDirectory(gridDir);

        var copied = 0;
        copied += CopyIfExists(artwork.Capsule, Path.Combine(gridDir, $"{appId}p.png"));
        copied += CopyIfExists(artwork.Horizontal, Path.Combine(gridDir, $"{appId}.png"));
        copied += CopyIfExists(artwork.Hero, Path.Combine(gridDir, $"{appId}_hero.png"));
        copied += CopyIfExists(artwork.Logo, Path.Combine(gridDir, $"{appId}_logo.png"));

        return copied;
    }

    /// <summary>
    /// Copies a shortcut's icon into Steam's grid folder so it keeps working after its
    /// original source (e.g. a removable SD card) is no longer available.
    /// </summary>
    public string? ApplyIcon(string steamPath, string userId, uint appId, string? sourceIconPath)
    {
        if (string.IsNullOrWhiteSpace(sourceIconPath) || !fileSystem.FileExists(sourceIconPath))
            return null;

        var gridDir = Path.Combine(steamPath, "userdata", userId, "config", "grid");
        fileSystem.CreateDirectory(gridDir);

        var destination = Path.Combine(gridDir, $"{appId}_icon{Path.GetExtension(sourceIconPath)}");
        fileSystem.CopyFile(sourceIconPath, destination, overwrite: true);
        return destination;
    }

    private int CopyIfExists(string? source, string destination)
    {
        if (string.IsNullOrWhiteSpace(source) || !fileSystem.FileExists(source))
            return 0;

        fileSystem.CopyFile(source, destination, overwrite: true);
        return 1;
    }
}
