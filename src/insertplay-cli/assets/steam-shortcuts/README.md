# Steam Shortcuts Assets (InsertPlay CLI)

This folder contains the assets used by the bootstrap shortcuts:
- Start SD Card
- Install SD Card

Current bootstrap status in setup:
- Start SD Card: implemented
- Install SD Card: planned for a future stage

When the project is compiled/published, all the contents of this folder will be copied to the output directory, allowing the user to replace the files manually.

## Expected file names

Use exactly the names below:

## Mapping (Steam -> file)

Each shortcut can use up to 5 assets:

- cover (vertical): `*-capsule.png`
- background (hero): `*-hero.png`
- logo: `*-logo.png`
- horizontal cover: `*-horizontal.png`
- icon: `*-icon.png`

### Start SD Card
- start-sd-capsule.png
- start-sd-hero.png
- start-sd-logo.png
- start-sd-horizontal.png
- start-sd-icon.png

## Rules

- Format: PNG
- It is recommended to maintain transparency when applicable
- Avoid changing file names

## Note

If a file is missing, the CLI may use a fallback or skip the application of that asset, depending on the implementation of the command that creates/updates shortcuts.

In the current format of the Steam module:
- vertical cover, horizontal cover, background and logo enter the Steam grid
- icon is associated with the shortcut's icon field
