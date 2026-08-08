using InsertPlay.Cli.Application.UseCases.Steam;

namespace InsertPlay.Cli.Commands;

internal sealed class SteamCommand(RestartSteamUseCase useCase) : ICommand
{
    public int Execute(string[] argv)
    {
        if (argv.Length < 2)
        {
            Console.Error.WriteLine("Usage: insertplay steam restart");
            return 1;
        }

        return argv[1] switch
        {
            "restart" => Restart(),
            _ => 1
        };
    }

    private int Restart()
    {
        var result = useCase.Execute();
        if (!result.Success)
        {
            Console.Error.WriteLine(result.Message);
            return result.ExitCode;
        }

        Console.WriteLine(result.Message);
        return 0;
    }
}
