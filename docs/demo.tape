Output docs/demo.gif
Set Shell "bash"
Set FontSize 15
Set Width 1120
Set Height 820
Set Padding 16
Set Theme "Catppuccin Mocha"
Set TypingSpeed 40ms
Set PlaybackSpeed 1
Env PS1 "$ "
Hide
Wait+Line@15s /\$$/
Type "export PS1='$ '; clear"
Enter
Wait+Line@15s /\$$/
Type "feedtick demo --pause 2"
Enter
Wait+Screen@20s /Confirm: feedtick ack/
Show
Wait+Screen@20s /No new headlines from available sources\./
Sleep 5s
