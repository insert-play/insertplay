using InsertPlay.Cli.Application.Abstractions;
using InsertPlay.Cli.Domain.Configuration;

namespace InsertPlay.Cli.Application.UseCases.Runtime;

public sealed record AutorunLaunchResult(int ExitCode, IReadOnlyList<string> InfoLines, string? ErrorMessage)
{
    public static AutorunLaunchResult Error(string message, int exitCode, IReadOnlyList<string>? infoLines = null) =>
        new(exitCode, infoLines ?? [], message);

    public static AutorunLaunchResult Success(IReadOnlyList<string> infoLines) => new(0, infoLines, null);
}

public sealed class AutorunLaunchUseCase(
    IConfigStore configStore,
    IShortcutRepository shortcutRepository,
    IShortcutIdentityStore identityStore,
    IEnvironmentFileStore environmentFileStore,
    IFileSystem fileSystem,
    ISteamProcessController steamProcessController,
    IProcessMonitor processMonitor)
{
    public AutorunLaunchResult Execute(string? sdRootRaw, string? explicitGuid)
    {
        if (string.IsNullOrWhiteSpace(sdRootRaw))
            return AutorunLaunchResult.Error("Usage: insertplay runtime autorun-launch --sd-root <path> [--guid <guid>]", 1);

        var sdRoot = fileSystem.GetFullPath(sdRootRaw);
        if (!fileSystem.DirectoryExists(sdRoot))
            return AutorunLaunchResult.Error($"SD root not found: {sdRoot}", 1);

        var infoLines = new List<string>();

        var (shortcutGuid, shortcutGuidMessage) = ResolveShortcutGuid(explicitGuid, sdRoot);
        if (!string.IsNullOrWhiteSpace(shortcutGuidMessage))
            infoLines.Add($"[insertplay runtime] {shortcutGuidMessage}");

        if (string.IsNullOrWhiteSpace(shortcutGuid))
            return AutorunLaunchResult.Error(
                "No specific shortcut identity found for this SD card. Run 'insertplay runtime install-shortcut' first.", 10, infoLines);

        if (!TryReadSpecificRuntimeLaunch(
                shortcutGuid, out var specificGameId, out var specificProcessName, out var specificTimeoutSeconds, out var hasVerification, out var specificLaunchError))
            return AutorunLaunchResult.Error(specificLaunchError, 10, infoLines);

        var cfg = configStore.TryLoad();
        if (configStore.IsValid(cfg, out _) && !SpecificShortcutExistsInSteam(cfg!, specificGameId))
            return AutorunLaunchResult.Error(
                $"Specific shortcut guid={shortcutGuid} not found in Steam shortcuts. Run 'insertplay runtime install-shortcut' again.", 11, infoLines);

        infoLines.Add($"[insertplay runtime] Starting specific shortcut guid={shortcutGuid} gameId={specificGameId}");
        if (!steamProcessController.LaunchGame(specificGameId, out var specificLaunchMessage))
            return AutorunLaunchResult.Error($"Failed to launch specific shortcut: {specificLaunchMessage}", 16, infoLines);

        if (hasVerification)
        {
            if (!processMonitor.WaitForProcessPattern(specificProcessName, specificTimeoutSeconds))
                return AutorunLaunchResult.Error(
                    $"Specific process (pattern: {specificProcessName}) was not detected within {specificTimeoutSeconds}s.", 16, infoLines);
        }
        else
        {
            infoLines.Add("[insertplay runtime] No launch verification configured; skipping process check.");
        }

        infoLines.Add("[insertplay runtime] Specific launch succeeded.");
        return AutorunLaunchResult.Success(infoLines);
    }

    private (string? Guid, string Message) ResolveShortcutGuid(string? explicitGuid, string sdRoot)
    {
        if (!string.IsNullOrWhiteSpace(explicitGuid))
        {
            return Guid.TryParse(explicitGuid, out var parsedGuid)
                ? (parsedGuid.ToString("D"), string.Empty)
                : (null, $"Provided shortcut GUID is invalid: {explicitGuid}.");
        }

        if (identityStore.TryRead(sdRoot, out var guid, out var error))
            return (guid, string.Empty);

        return (null, error);
    }

    private bool TryReadSpecificRuntimeLaunch(
        string shortcutGuid,
        out string gameId,
        out string processName,
        out int timeoutSeconds,
        out bool hasVerification,
        out string error)
    {
        gameId = string.Empty;
        processName = string.Empty;
        timeoutSeconds = 0;
        hasVerification = false;
        error = string.Empty;

        string path;
        try
        {
            path = configStore.GetRuntimeShortcutEnvPath(shortcutGuid);
        }
        catch (ArgumentException ex)
        {
            error = ex.Message;
            return false;
        }

        if (!environmentFileStore.TryRead(path, out var values, out var envError))
        {
            error = envError;
            return false;
        }

        gameId = GetEnvValue(values, "INSERTPLAY_SPECIFIC_GAME_ID");
        processName = GetEnvValue(values, "INSERTPLAY_SPECIFIC_PROCESS_NAME");
        var timeoutRaw = GetEnvValue(values, "INSERTPLAY_SPECIFIC_TIMEOUT_SECONDS");

        if (string.IsNullOrWhiteSpace(gameId))
        {
            error = $"Specific runtime shortcut data is incomplete for guid={shortcutGuid}.";
            return false;
        }

        if (string.IsNullOrWhiteSpace(processName))
        {
            hasVerification = false;
            timeoutSeconds = 0;
            return true;
        }

        if (!int.TryParse(timeoutRaw, out timeoutSeconds) || timeoutSeconds <= 0)
        {
            error = $"Specific runtime shortcut data is incomplete for guid={shortcutGuid}.";
            return false;
        }

        hasVerification = true;
        return true;
    }

    private bool SpecificShortcutExistsInSteam(AppConfig cfg, string specificGameId)
    {
        if (!ulong.TryParse(specificGameId, out var parsedGameId))
            return false;

        var appId = (uint)(parsedGameId >> 32);
        return shortcutRepository.GetAll(cfg.SteamPath, cfg.SteamUserId).Any(item => item.AppId == appId);
    }

    private static string GetEnvValue(IReadOnlyDictionary<string, string> vars, string key) =>
        vars.TryGetValue(key, out var value) ? value : string.Empty;
}
