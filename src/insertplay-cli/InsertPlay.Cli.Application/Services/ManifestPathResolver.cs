using InsertPlay.Cli.Application.Abstractions;

namespace InsertPlay.Cli.Application.Services;

internal static class ManifestPathResolver
{
    public static string? Resolve(IFileSystem fileSystem, string sdRoot)
    {
        var rootPath = Path.Combine(sdRoot, "steam-shortcut.json");
        if (fileSystem.FileExists(rootPath))
            return rootPath;

        var legacyPath = Path.Combine(sdRoot, "insertplay", "steam-shortcut.json");
        return fileSystem.FileExists(legacyPath) ? legacyPath : null;
    }
}
