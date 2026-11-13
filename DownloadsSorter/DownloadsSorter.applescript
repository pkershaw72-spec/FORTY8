-- Downloads Sorter: asks, then sorts the loose files in ~/Downloads into
-- category subfolders. The work is done by the downloads-sorter shell script
-- that install.sh puts in ~/Library/Application Support/DownloadsSorter.

on run
	set appTitle to "Downloads Sorter"
	set supportFolder to POSIX path of (path to application support folder from user domain)
	set tool to "/bin/bash " & quoted form of (supportFolder & "DownloadsSorter/downloads-sorter")
	
	try
		set planLines to paragraphs of (do shell script tool & " --plan")
	on error errorText
		display dialog errorText with title appTitle buttons {"OK"} default button "OK" with icon caution
		return
	end try
	
	set total to (item 1 of planLines) as integer
	set canUndo to (item 2 of planLines) is "undo"
	set summary to ""
	repeat with i from 3 to (count of planLines)
		set summary to summary & (item i of planLines) & return
	end repeat
	
	if total is 0 then
		set message to "Nothing to sort. Your Downloads folder has no loose files."
		if canUndo then
			set choice to button returned of (display dialog message with title appTitle buttons {"Undo Last Sort", "OK"} default button "OK")
		else
			display dialog message with title appTitle buttons {"OK"} default button "OK"
			return
		end if
	else
		set message to "Found " & total & " file(s) to sort into subfolders of Downloads:" & return & return & summary & return & "Folders and downloads still in progress are left alone."
		if canUndo then
			set choice to button returned of (display dialog message with title appTitle buttons {"Cancel", "Undo Last Sort", "Sort"} default button "Sort" cancel button "Cancel")
		else
			set choice to button returned of (display dialog message with title appTitle buttons {"Cancel", "Sort"} default button "Sort" cancel button "Cancel")
		end if
	end if
	
	try
		if choice is "Undo Last Sort" then
			set outcome to do shell script tool & " --undo"
			display dialog outcome with title appTitle buttons {"OK"} default button "OK"
		else if choice is "Sort" then
			set outcome to do shell script tool & " --yes"
			set choice to button returned of (display dialog outcome with title appTitle buttons {"OK", "Show Downloads"} default button "Show Downloads")
			if choice is "Show Downloads" then do shell script "open \"$HOME/Downloads\""
		end if
	on error errorText number errorNumber
		if errorNumber is not -128 then display dialog errorText with title appTitle buttons {"OK"} default button "OK" with icon caution
	end try
end run
