using InsertPlay.Cli.Application.Abstractions;
using InsertPlay.Cli.Domain.Configuration;
using InsertPlay.Cli.Domain.Steam;

namespace InsertPlay.Cli.Application.Services;

/// <summary>
/// Writes the generic (non-specific) runtime environment file consumed by autorun.sh on the SD card.
/// Shared by the setup and configure use cases, which both need to (re)compute it after saving AppConfig.
/// </summary>
public sealed class RuntimeEnvironmentWriter(IConfigStore configStore)
{
    public string Save(AppConfig cfg, uint appId)
    {
        var gameId = GameIdCalculator.Calculate(appId);
        var variables = new Dictionary<string, string>
        {
            ["INSERTPLAY_SD_APP_ID"] = appId.ToString(),
            ["INSERTPLAY_SD_GAME_ID"] = gameId.ToString(),
            ["INSERTPLAY_STEAM_PATH"] = cfg.SteamPath,
            ["INSERTPLAY_STEAM_USER_ID"] = cfg.SteamUserId
        };

        configStore.SaveRuntimeEnvironment(variables);
        return configStore.GetRuntimeEnvPath();
    }
}
