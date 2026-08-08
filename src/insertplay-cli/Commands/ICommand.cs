namespace InsertPlay.Cli.Commands;

internal interface ICommand
{
    int Execute(string[] argv);
}
