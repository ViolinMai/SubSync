menu(type='*' title='SubSync Suite' image='A:\LINUX-WIN\Codes\Python\SubSync\libs\icon.ico')
{
    item(title='🎙️ Open in Studio' cmd='python.exe' args='"A:\LINUX-WIN\Codes\Python\SubSync\main.py" --tab studio "@sel.path"')
    item(title='🎬 Send to Subtitle Merger' cmd='python.exe' args='"A:\LINUX-WIN\Codes\Python\SubSync\main.py" --tab merger "@sel.path"')
    item(title='🏷️ Open in Bulk Renamer' cmd='python.exe' args='"A:\LINUX-WIN\Codes\Python\SubSync\main.py" --tab renamer "@sel.path"')
    item(title='🔍 Inspect with MediaInfo' cmd='python.exe' args='"A:\LINUX-WIN\Codes\Python\SubSync\main.py" --tab mediainfo "@sel.path"')
}

menu(type='directory|back' title='SubSync Suite' image='A:\LINUX-WIN\Codes\Python\SubSync\libs\icon.ico')
{
    item(title='🎬 Open Folder in Merger' cmd='python.exe' args='"A:\LINUX-WIN\Codes\Python\SubSync\main.py" --tab merger "@sel.path"')
    item(title='🏷️ Bulk Rename Directory' cmd='python.exe' args='"A:\LINUX-WIN\Codes\Python\SubSync\main.py" --tab renamer "@sel.path"')
}