using InsertPlay.Cli.Application.Abstractions;
using InsertPlay.Cli.Domain.Steam;

namespace InsertPlay.Cli.Application.UseCases.Shortcuts;

public sealed record ListShortcutsResult(bool Success, string? Error, int ExitCode, IReadOnlyList<SteamShortcutEntry> Items);

public sealed class ListShortcutsUseCase(IConfigStore configStore, IShortcutRepository shortcutRepository)
{
    public ListShortcutsResult Execute()
    {
        var cfg = configStore.TryLoad();
        if (!configStore.IsValid(cfg, out var configError))
            return new ListShortcutsResult(false, configError, 1, []);

        var items = shortcutRepository.GetAll(cfg!.SteamPath, cfg.SteamUserId)
            .OrderBy(i => i.AppName, StringComparer.OrdinalIgnoreCase)
            .ToList();

        return new ListShortcutsResult(true, null, 0, items);
    }
}
