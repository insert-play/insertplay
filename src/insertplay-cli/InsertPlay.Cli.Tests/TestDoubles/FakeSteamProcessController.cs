using InsertPlay.Cli.Application.Abstractions;

namespace InsertPlay.Cli.Tests.TestDoubles;

internal sealed class FakeSteamProcessController : ISteamProcessController
{
    public bool RestartResult { get; set; } = true;
    public string RestartMessage { get; set; } = "restarted";
    public bool LaunchGameResult { get; set; } = true;
    public string LaunchGameMessage { get; set; } = "launched";
    public List<string> LaunchedGameIds { get; } = [];

    public bool Restart(string steamPath, out string message)
    {
        message = RestartMessage;
        return RestartResult;
    }

    public bool LaunchGame(string gameId, out string message)
    {
        LaunchedGameIds.Add(gameId);
        message = LaunchGameMessage;
        return LaunchGameResult;
    }
}
