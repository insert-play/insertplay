using InsertPlay.Cli.Application.Abstractions;

namespace InsertPlay.Cli.Tests.TestDoubles;

internal sealed class FakeProcessMonitor : IProcessMonitor
{
    public bool IsRunningResult { get; set; }
    public bool WaitResult { get; set; }

    public bool IsProcessRunningPattern(string processPattern) => IsRunningResult;

    public bool WaitForProcessPattern(string processPattern, int timeoutSeconds) => WaitResult;
}
