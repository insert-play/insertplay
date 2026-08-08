using InsertPlay.Cli.Domain.Steam;

namespace InsertPlay.Cli.Application.Abstractions;

public interface IShortcutManifestRepository
{
    bool TryLoad(string path, out SteamShortcutManifest? manifest, out string error);
}
