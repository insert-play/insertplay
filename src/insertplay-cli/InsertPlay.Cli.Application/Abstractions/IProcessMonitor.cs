namespace InsertPlay.Cli.Application.Abstractions;

public interface IProcessMonitor
{
    bool IsProcessRunningPattern(string processPattern);

    bool WaitForProcessPattern(string processPattern, int timeoutSeconds);
}
