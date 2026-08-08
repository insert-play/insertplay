using System.Text.Json;
using InsertPlay.Cli.Application.Abstractions;
using InsertPlay.Cli.Application.Services;
using InsertPlay.Cli.Domain.Steam;

namespace InsertPlay.Cli.Infrastructure.Manifest;

public sealed class JsonShortcutManifestRepository(IFileSystem fileSystem) : IShortcutManifestRepository
{
    public bool TryLoad(string path, out SteamShortcutManifest? manifest, out string error)
    {
        manifest = null;
        error = string.Empty;

        try
        {
            var json = fileSystem.ReadAllText(path);
            manifest = JsonSerializer.Deserialize<SteamShortcutManifest>(json, new JsonSerializerOptions
            {
                PropertyNameCaseInsensitive = true
            });
        }
        catch (Exception ex)
        {
            error = $"Failed to parse manifest: {ex.Message}";
            return false;
        }

        return SteamShortcutManifestValidator.Validate(manifest, out error);
    }
}
