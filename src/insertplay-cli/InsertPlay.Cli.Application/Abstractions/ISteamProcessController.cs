namespace InsertPlay.Cli.Application.Abstractions;

public interface ISteamProcessController
{
    bool Restart(string steamPath, out string message);

    bool LaunchGame(string gameId, out string message);
}
