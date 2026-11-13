# Downloads Sorter

A small Mac app that tidies your Downloads folder. Open it, confirm, and every
loose file is moved into a category subfolder of Downloads: Images, Videos,
Audio, PDFs, Documents, Spreadsheets, Presentations, Archives, Installers, eBooks,
Fonts, Calendar and Contacts, Code and Data, and Other for anything unrecognised.

It is unrelated to the rest of this repository; it just lives here.

## Install

The app is built on your own Mac, so macOS treats it as yours: no malware
warning, and it can ask properly for access to Downloads.

1. Open Terminal.
2. Type `bash ` (with a space after it), drag `install.sh` from this folder
   into the Terminal window, and press Return.
3. Downloads Sorter opens. Click Allow when macOS asks about your Downloads
   folder.

After that it is in Applications as "Downloads Sorter".

## What it does

- Shows how many files it found per category and asks before moving anything.
- Leaves folders, apps, hidden files and downloads still in progress alone.
- Never overwrites: a clashing name becomes `name (1).ext`.
- **Undo Last Sort** puts the files from the most recent sort back.

## Changing the categories

The rules are a plain list of file extensions in the `category_for` function of
`downloads-sorter`. Edit it in any text editor and run `install.sh` again.

## Files

- `DownloadsSorter.applescript`: the app (the dialogs).
- `downloads-sorter`: the shell script that plans, sorts and undoes.
- `install.sh`: builds the app with `osacompile` and installs both.
