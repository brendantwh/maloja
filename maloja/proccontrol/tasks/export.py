import json
import os
import time

from doreah.io import col

def export(targetfolder=None):

	from ...database.sqldb import get_scrobbles, export_aliases
	from ...database import rules

	if targetfolder is None:
		targetfolder = os.getcwd()

	timestr = time.strftime("%Y_%m_%d_%H_%M_%S")
	timestamp = int(time.time()) # ok this is technically a separate time get from above, but those ms are not gonna matter, and im too lazy to change it all to datetime
	filename = f"maloja_export_{timestr}.json"
	outputfile = os.path.join(targetfolder,filename)
	assert not os.path.exists(outputfile)

	data = {'maloja':{'export_time': timestamp },'scrobbles':get_scrobbles(),'rules':rules.export_data(),'aliases':export_aliases()}
	with open(outputfile,'w') as outfd:
		json.dump(data,outfd,indent=3)

	print(f"Exported {len(data['scrobbles'])} Scrobbles, {len(data['rules']['rules'])} Rules and {len(data['aliases'])} Aliases to {col['yellow'](outputfile)}")
	return outputfile
