using InsertPlay.Cli.Application.Abstractions;
using InsertPlay.Cli.Application.Services;

namespace InsertPlay.Cli.Application.UseCases.Runtime;

public sealed record EnsureIdentityResult(
    bool Success,
    string? ErrorMessage,
    int ExitCode,
    string? ShortcutGuid,
    string? IdentityPath);

public sealed class EnsureShortcutIdentityUseCase(
    IFileSystem fileSystem,
    IShortcutManifestRepository manifestRepository,
    IShortcutIdentityStore identityStore)
{
    public EnsureIdentityResult Execute(string? sdRootRaw)
    {
        if (string.IsNullOrWhiteSpace(sdRootRaw))
            return new EnsureIdentityResult(false, "Usage: insertplay runtime ensure-identity --sd-root <path>", 1, null, null);

        var sdRoot = fileSystem.GetFullPath(sdRootRaw);
        if (!fileSystem.DirectoryExists(sdRoot))
            return new EnsureIdentityResult(false, $"SD root not found: {sdRoot}", 1, null, null);

        var appName = "InsertPlay Game";
        string? manifestGuid = null;

        var manifestPath = ManifestPathResolver.Resolve(fileSystem, sdRoot);
        if (!string.IsNullOrWhiteSpace(manifestPath)
            && manifestRepository.TryLoad(manifestPath, out var manifest, out _)
            && manifest?.Shortcut is not null)
        {
            appName = string.IsNullOrWhiteSpace(manifest.Shortcut.AppName)
                ? appName
                : manifest.Shortcut.AppName.Trim();
            manifestGuid = string.IsNullOrWhiteSpace(manifest.Shortcut.Guid)
                ? null
                : manifest.Shortcut.Guid.Trim();
        }

        if (!identityStore.TryGetOrCreate(sdRoot, appName, manifestGuid, out var shortcutGuid, out var identityError))
            return new EnsureIdentityResult(false, identityError, 12, null, null);

        var identityPath = Path.Combine(sdRoot, "shortcut-identity.json");
        return new EnsureIdentityResult(true, null, 0, shortcutGuid, identityPath);
    }
}
