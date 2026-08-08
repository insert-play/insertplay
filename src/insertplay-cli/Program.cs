using InsertPlay.Cli;
using Microsoft.Extensions.DependencyInjection;

using var provider = CompositionRoot.Build();
var router = provider.GetRequiredService<CommandRouter>();
return router.Run(args);
