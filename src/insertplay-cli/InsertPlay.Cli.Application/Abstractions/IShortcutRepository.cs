using InsertPlay.Cli.Domain.Steam;

namespace InsertPlay.Cli.Application.Abstractions;

public interface IShortcutRepository
{
    IReadOnlyList<SteamShortcutEntry> GetAll(string steamPath, string userId);

    bool AddOrUpdate(string steamPath, string userId, SteamShortcutEntry entry, out string message);

    bool Remove(string steamPath, string userId, string appName, out string message);
}
