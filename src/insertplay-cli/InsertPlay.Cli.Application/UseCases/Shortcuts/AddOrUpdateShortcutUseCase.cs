using InsertPlay.Cli.Application.Abstractions;
using InsertPlay.Cli.Domain.Steam;

namespace InsertPlay.Cli.Application.UseCases.Shortcuts;

public sealed record ShortcutOperationResult(bool Success, string Message, int ExitCode);

public sealed class AddOrUpdateShortcutUseCase(IConfigStore configStore, IShortcutRepository shortcutRepository)
{
    public ShortcutOperationResult Execute(
        string name,
        string exe,
        string startDir,
        string launchOptions,
        string icon,
        bool isHidden)
    {
        var cfg = configStore.TryLoad();
        if (!configStore.IsValid(cfg, out var configError))
            return new ShortcutOperationResult(false, configError, 1);

        var entry = new SteamShortcutEntry
        {
            AppId = AppIdCalculator.Calculate(exe, name),
            AppName = name,
            Exe = exe,
            StartDir = startDir,
            LaunchOptions = launchOptions,
            Icon = icon,
            IsHidden = isHidden
        };

        var ok = shortcutRepository.AddOrUpdate(cfg!.SteamPath, cfg.SteamUserId, entry, out var message);
        return new ShortcutOperationResult(ok, message, ok ? 0 : 5);
    }
}
