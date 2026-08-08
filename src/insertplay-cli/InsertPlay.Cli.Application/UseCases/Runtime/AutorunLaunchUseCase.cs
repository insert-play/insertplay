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

        var genericRuntimeEnvPath = configStore.GetRuntimeEnvPath();
        if (!environmentFileStore.TryRead(genericRuntimeEnvPath, out var runtimeVars, out var runtimeEnvError))
            return AutorunLaunchResult.Error(runtimeEnvError, 10);

        var genericGameId = GetEnvValue(runtimeVars, "INSERTPLAY_SD_GAME_ID");
        if (string.IsNullOrWhiteSpace(genericGameId))
            return AutorunLaunchResult.Error("InsertPlay runtime configuration not found. Run 'insertplay setup' first.", 10);

        var infoLines = new List<string>();

        var (shortcutGuid, shortcutGuidMessage) = ResolveShortcutGuid(explicitGuid, sdRoot);
        if (!string.IsNullOrWhiteSpace(shortcutGuidMessage))
            infoLines.Add($"[insertplay runtime] {shortcutGuidMessage}");

        var cfg = configStore.TryLoad();
        var hasValidConfig = configStore.IsValid(cfg, out _);

        var specificLaunchError = string.Empty;
        var specificGameId = string.Empty;
        var specificProcessName = string.Empty;
        var specificTimeoutSeconds = 0;
        var attemptedSpecific = false;

        if (!string.IsNullOrWhiteSpace(shortcutGuid))
        {
            attemptedSpecific = TryReadSpecificRuntimeLaunch(
                shortcutGuid, out specificGameId, out specificProcessName, out specificTimeoutSeconds, out specificLaunchError);
        }

        if (attemptedSpecific)
        {
            var specificExists = true;
            if (hasValidConfig)
                specificExists = SpecificShortcutExistsInSteam(cfg!, specificGameId);

            if (!specificExists)
            {
                infoLines.Add($"[insertplay runtime] Specific shortcut guid={shortcutGuid} not found in Steam shortcuts. Falling back to generic shortcut.");
            }
            else
            {
                infoLines.Add($"[insertplay runtime] Trying specific shortcut guid={shortcutGuid} gameId={specificGameId}");
                if (steamProcessController.LaunchGame(specificGameId, out var specificLaunchMessage))
                {
                    if (processMonitor.WaitForProcessPattern(specificProcessName, specificTimeoutSeconds))
                    {
                        infoLines.Add("[insertplay runtime] Specific launch succeeded.");
                        return AutorunLaunchResult.Success(infoLines);
                    }

                    infoLines.Add("[insertplay runtime] Specific process was not detected in time. Falling back to generic shortcut.");
                }
                else
                {
                    infoLines.Add($"[insertplay runtime] Failed to request specific launch: {specificLaunchMessage}. Falling back to generic shortcut.");
                }
            }
        }
        else if (!string.IsNullOrWhiteSpace(specificLaunchError))
        {
            infoLines.Add($"[insertplay runtime] {specificLaunchError} Falling back to generic shortcut.");
        }
        else
        {
            infoLines.Add("[insertplay runtime] Specific runtime shortcut env not available. Falling back to generic shortcut.");
        }

        infoLines.Add($"[insertplay runtime] Starting generic shortcut gameId={genericGameId}");
        if (steamProcessController.LaunchGame(genericGameId, out var genericLaunchMessage))
            return AutorunLaunchResult.Success(infoLines);

        return AutorunLaunchResult.Error($"Failed to launch generic shortcut: {genericLaunchMessage}", 16, infoLines);
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
        out string error)
    {
        gameId = string.Empty;
        processName = string.Empty;
        timeoutSeconds = 0;
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

        if (string.IsNullOrWhiteSpace(gameId)
            || string.IsNullOrWhiteSpace(processName)
            || !int.TryParse(timeoutRaw, out timeoutSeconds)
            || timeoutSeconds <= 0)
        {
            error = $"Specific runtime shortcut data is incomplete for guid={shortcutGuid}.";
            return false;
        }

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
