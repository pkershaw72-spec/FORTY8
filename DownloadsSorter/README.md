# Downloads Sorter

A small Mac app that tidies your Downloads folder. Double-click it, confirm, and
every loose file is moved into a category subfolder of Downloads: Images, Videos,
Audio, PDFs, Documents, Spreadsheets, Presentations, Archives, Installers, eBooks,
Fonts, Calendar and Contacts, Code and Data, and Other for anything unrecognised.

It is unrelated to the rest of this repository; it just lives here.

## Install

1. Copy `DownloadsSorter.app` to your Applications folder (or anywhere you like).
2. The app is not signed, so the first launch needs one extra step. Either
   right-click the app and choose Open, or run:

   ```bash
   xattr -dr com.apple.quarantine /Applications/DownloadsSorter.app
   ```

3. The first time it runs, macOS asks whether it may access your Downloads
   folder. Choose Allow.

## What it does

- Shows how many files it found per category and asks before moving anything.
- Leaves folders, apps, hidden files and downloads still in progress alone.
- Never overwrites: a clashing name becomes `name (1).ext`.
- **Undo Last Sort** puts the files from the most recent sort back.

## Changing the categories

The rules are a plain list of file extensions in
`DownloadsSorter.app/Contents/MacOS/downloads-sorter` (the `category_for`
function). Edit it in any text editor.

## From Terminal

```bash
/Applications/DownloadsSorter.app/Contents/MacOS/downloads-sorter --yes
```

sorts without dialogs; `--undo` reverses the last sort.
