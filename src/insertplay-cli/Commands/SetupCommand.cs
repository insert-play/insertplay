using InsertPlay.Cli.Application.UseCases.Setup;

namespace InsertPlay.Cli.Commands;

internal sealed class SetupCommand(SetupUseCase useCase) : ICommand
{
    public int Execute(string[] argv)
    {
        var lookup = useCase.GetAvailableUsers();
        if (!lookup.Success)
        {
            Console.Error.WriteLine(lookup.Error);
            return lookup.ExitCode;
        }

        Console.WriteLine("Steam users found:\n");
        for (var i = 0; i < lookup.Users.Count; i++)
            Console.WriteLine($"[{i}] {lookup.Users[i].PersonaName} ({lookup.Users[i].UserId})");

        Console.Write("\nChoose a user: ");
        var choice = Console.ReadLine();
        if (!int.TryParse(choice, out var index) || index < 0 || index >= lookup.Users.Count)
        {
            Console.Error.WriteLine("Invalid index.");
            return 4;
        }

        var selected = lookup.Users[index];
        var result = useCase.CompleteSetup(lookup.SteamRoot!, selected);

        Console.WriteLine($"Configuration saved to {result.ConfigPath}");

        foreach (var line in result.ShortcutBootstrap.InfoLines)
            Console.WriteLine(line);

        if (result.ShortcutBootstrap.ErrorMessage is not null)
            Console.Error.WriteLine(result.ShortcutBootstrap.ErrorMessage);

        return 0;
    }
}
