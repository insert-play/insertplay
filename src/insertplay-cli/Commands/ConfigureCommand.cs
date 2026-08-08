using InsertPlay.Cli.Application.UseCases.Configure;
using InsertPlay.Cli.Common;

namespace InsertPlay.Cli.Commands;

internal sealed class ConfigureCommand(ConfigureUseCase useCase) : ICommand
{
    public int Execute(string[] argv)
    {
        var userId = ArgvParser.GetOption(argv, "--user");
        if (string.IsNullOrWhiteSpace(userId))
        {
            Console.Error.WriteLine("Usage: insertplay configure --user <steamUserId>");
            return 1;
        }

        var result = useCase.Execute(userId);
        if (!result.Success)
        {
            Console.Error.WriteLine(result.Error);
            return result.ExitCode;
        }

        Console.WriteLine("Configuration updated.");
        return 0;
    }
}
