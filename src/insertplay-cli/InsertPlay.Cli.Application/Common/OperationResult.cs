namespace InsertPlay.Cli.Application.Common;

public sealed record OperationResult(bool Success, string Message, int ExitCode)
{
    public static OperationResult Ok(string message, int exitCode = 0) => new(true, message, exitCode);

    public static OperationResult Fail(string message, int exitCode) => new(false, message, exitCode);
}
