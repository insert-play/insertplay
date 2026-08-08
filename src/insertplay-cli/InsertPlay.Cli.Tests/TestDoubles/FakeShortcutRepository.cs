using InsertPlay.Cli.Application.Abstractions;
using InsertPlay.Cli.Domain.Steam;

namespace InsertPlay.Cli.Tests.TestDoubles;

internal sealed class FakeShortcutRepository : IShortcutRepository
{
    public List<SteamShortcutEntry> Entries { get; } = [];
    public bool AddOrUpdateResult { get; set; } = true;
    public string AddOrUpdateMessage { get; set; } = "ok";
    public bool RemoveResult { get; set; } = true;
    public string RemoveMessage { get; set; } = "removed";
    public SteamShortcutEntry? LastAddedOrUpdated { get; private set; }

    public IReadOnlyList<SteamShortcutEntry> GetAll(string steamPath, string userId) => Entries;

    public bool AddOrUpdate(string steamPath, string userId, SteamShortcutEntry entry, out string message)
    {
        LastAddedOrUpdated = entry;
        message = AddOrUpdateMessage;
        if (AddOrUpdateResult)
            Entries.Add(entry);
        return AddOrUpdateResult;
    }

    public bool Remove(string steamPath, string userId, string appName, out string message)
    {
        message = RemoveMessage;
        return RemoveResult;
    }
}
