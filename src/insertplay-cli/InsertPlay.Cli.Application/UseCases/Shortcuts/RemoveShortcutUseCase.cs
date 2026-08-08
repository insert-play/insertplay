using InsertPlay.Cli.Application.Abstractions;

namespace InsertPlay.Cli.Application.UseCases.Shortcuts;

public sealed class RemoveShortcutUseCase(IConfigStore configStore, IShortcutRepository shortcutRepository)
{
    public ShortcutOperationResult Execute(string name)
    {
        var cfg = configStore.TryLoad();
        if (!configStore.IsValid(cfg, out var configError))
            return new ShortcutOperationResult(false, configError, 1);

        var ok = shortcutRepository.Remove(cfg!.SteamPath, cfg.SteamUserId, name, out var message);
        return new ShortcutOperationResult(ok, message, ok ? 0 : 6);
    }
}
