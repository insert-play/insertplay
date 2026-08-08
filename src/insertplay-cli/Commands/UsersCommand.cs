using InsertPlay.Cli.Application.UseCases.Users;

namespace InsertPlay.Cli.Commands;

internal sealed class UsersCommand(ListUsersUseCase useCase) : ICommand
{
    public int Execute(string[] argv)
    {
        var result = useCase.Execute();
        if (!result.SteamFound)
        {
            Console.Error.WriteLine(result.Error);
            return result.ExitCode;
        }

        if (result.Users.Count == 0)
        {
            Console.WriteLine("No Steam users found.");
            return 0;
        }

        foreach (var user in result.Users)
        {
            var marker = result.ConfiguredUserId == user.UserId ? "*" : " ";
            Console.WriteLine($"{marker} {user.PersonaName} ({user.UserId})");
        }

        return 0;
    }
}
