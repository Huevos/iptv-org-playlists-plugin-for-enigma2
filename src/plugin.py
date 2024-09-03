from urllib.request import urlopen
import json
from os import makedirs as os_makedirs
from requests import get, exceptions
import threading
from shutil import rmtree

from enigma import eDVBDB, eTimer

from Components.ActionMap import ActionMap
from Components.config import config, ConfigSubsection, ConfigText, configfile
from Components.SelectionList import SelectionList, SelectionEntryComponent
from Components.Sources.StaticText import StaticText
from Plugins.Plugin import PluginDescriptor
from Screens.MessageBox import MessageBox
from Screens.Screen import Screen, ScreenSummary
from Tools.CountryCodes import ISO3166


config.plugins.iptv_org = ConfigSubsection()
config.plugins.iptv_org.cc = ConfigText("", False)

repo = "github.com/iptv-org/iptv/tree/master/streams"  # master is default branch
repo_owner, repo_name, repo_branch, repo_path = (x := repo.split("/", 5))[1:3] + x[4:6]
data_path = f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents/{repo_path}"
download_path = f"https://raw.githubusercontent.com/{repo_owner}/{repo_name}/{repo_branch}/{repo_path}"
tempDir = "/var/volatile/tmp/iptv-org"


class Fetcher():
	def __init__(self):
		self.bouquetFilename = "userbouquet.iptv-org.%s.tv"
		self.bouquetName = _("iptv-org")
		self.cc = {x[1].lower():x[0] for x in ISO3166}
		self.downloaded = []
		
	def fetchJson(self, path):
		try:
			return json.load(urlopen(path))
		except Exception:
			import traceback
			traceback.print_exc()
		return []

	def getfilenames(self):
		self.filenames = []
		for item in self.fetchJson(data_path):
			if item.get("type") == "file":
				name = item.get("name")
				if name and name.endswith(".m3u"):
					self.filenames.append(name)
		return self.filenames

	@staticmethod
	def downloadPage(file, success, fail=None):
		link = download_path + "/" + file
		# link = link.encode('ascii', 'xmlcharrefreplace').decode().replace(' ', '%20').replace('\n', '')  # needed when using the api
		try:
			response = get(link, timeout=2.50)
			response.raise_for_status()
			with open(tempDir + "/" + file, "wb") as f:
				f.write(response.content)
			success(file)
		except exceptions.RequestException as error:
			if callable(fail):
				fail(error)

	def scrape(self):
		os_makedirs(tempDir, exist_ok=True)
		threads = [threading.Thread(target=self.downloadPage, args=(filename, self.success, self.failure)) for filename in self.filenames]
		for thread in threads:
			thread.start()
		for thread in threads:
			thread.join()
		print("[Fetcher] all fetched")

	def success(self, file):
		self.downloaded.append(file)

	def failure(self, error):
		print("[Fetcher] Error: %s" % error)

	def processfiles(self):
		known_urls = []
		self.channelcount = 0
		self.channels = {}
		for file in sorted(self.downloaded):
			country = self.cc.get(file[:2], file[:2])
			if country not in self.channels:
				self.channels[country] = []
			channelname = ""
			url = ""
			with open(tempDir + "/" + file, encoding='utf-8', errors="ignore") as f:
				for line in f:
					if line.startswith("#EXTINF:"):
						channelname = ""
						url = ""
						if len(line_split := line.rsplit(",", 1)) > 1:
							channelname = line_split[1].strip()  # .rsplit("(", 1)[0].strip()
					elif line.startswith("http"):
						url = line.strip()
					if channelname and url and url not in known_urls:
						self.channels[country].append((channelname, url))
						known_urls.append(url)
						channelname = ""
						url = ""
						self.channelcount += 1

	def createBouquet(self):
		for country in sorted(list(self.channels.keys()), key=lambda x: x[0].lower()):
			bouquet_list = []
			if self.channels[country]:  # country not empty
				bouquet_list.append("1:64:0:0:0:0:0:0:0:0:%s" % country)
				for channelname, url in sorted(self.channels[country]):
					bouquet_list.append("4097:0:1:1:1:1:CCCC0000:0:0:0:%s:%s" % (url.replace(":", "%3a"), channelname))
			if bouquet_list:
				eDVBDB.getInstance().addOrUpdateBouquet(self.bouquetName + " - " + country, self.bouquetFilename % country.split()[0].strip().lower(), bouquet_list, False)

	def cleanup(self):
		rmtree(tempDir)


class PluginSetup(Screen):
	def __init__(self, session):
		Screen.__init__(self, session)
		self.title = _("iptv-org playlists")
		self.skinName = ["Setup"]
		self.fetcher = Fetcher()
		self.filenames = []
		self.cc_options = []
		self.cc_enabled = []
		self["config"] = SelectionList([], enableWrapAround=True)
		self["key_red"] = StaticText(_("Cancel"))
		self["key_green"] = StaticText(_("Create bouquets"))
		self["key_yellow"] = StaticText(_("Toggle all"))
		self["description"] = StaticText("")
		self["actions"] = ActionMap(["SetupActions", "ColorActions"],
		{
			"ok": self["config"].toggleSelection,
			"save": self.keyCreate,
			"cancel": self.keyCancel,
			"yellow": self["config"].toggleAllSelection,
		}, -2)
		self.timer = eTimer()
		self.timer.callback.append(self.buildList)
		self.timer.start(10, 1)

	def buildList(self):
		self.filenames = self.fetcher.getfilenames()
		for fn in self.filenames:
			if fn[:2] not in self.cc_options:
				self.cc_options.append(fn[:2])
		self.cc_enabled = [x for x in config.plugins.iptv_org.cc.value.split("|") if x in self.cc_options]  # remove stale values
		self.cc_options.sort(key=lambda x: self.fetcher.cc.get(x, x).lower())
		self["config"].setList([SelectionEntryComponent(self.fetcher.cc.get(cc, cc), cc, "", cc in self.cc_enabled) for cc in self.cc_options])

	def readList(self):
		self.cc_enabled = [x[0][1] for x in self["config"].list if x[0][3]]
		config.plugins.iptv_org.cc.value = "|".join(self.cc_enabled)

	def keyCreate(self):
		self.readList()
		if self.cc_enabled:
			self["actions"].setEnabled(False)
			self.title += " - " + _("Creating bouquets")
			self["description"].text = _("Creating bouquets. This may take some time. Please be patient.")
			self["key_red"].text = ""
			self["key_green"].text = ""
			self["key_yellow"].text = ""
			self["config"].setList([])
			config.plugins.iptv_org.cc.save()
			configfile.save()
			self.runtimer = eTimer()
			self.runtimer.callback.append(self.doRun)
			self.runtimer.start(10, 1)
		else:
			self.session.open(MessageBox, _("Please select the bouquets you wish to create"))

	def doRun(self):
		self.fetcher.filenames = [x for x in self.fetcher.filenames if x[:2] in self.cc_enabled]
		self.fetcher.scrape()
		self.fetcher.processfiles()
		self.fetcher.createBouquet()
		self.fetcher.cleanup()
		self.close()

	def keyCancel(self):
		self.readList()
		if config.plugins.iptv_org.cc.isChanged():
			self.session.openWithCallback(self.cancelConfirm, MessageBox, _("Really close without saving settings?"))
		else:
			self.close()

	def cancelConfirm(self, result):
		if not result:
			return
		config.plugins.iptv_org.cc.cancel()
		self.close()

	def createSummary(self):
		return PluginSummary


class PluginSummary(ScreenSummary):
	def __init__(self, session, parent):
		ScreenSummary.__init__(self, session, parent=parent)
		self.skinName = "PluginBrowserSummary"
		self["entry"] = StaticText("")
		if self.addWatcher not in self.onShow:
			self.onShow.append(self.addWatcher)
		if self.removeWatcher not in self.onHide:
			self.onHide.append(self.removeWatcher)

	def addWatcher(self):
		if self.selectionChanged not in self.parent["config"].onSelectionChanged:
			self.parent["config"].onSelectionChanged.append(self.selectionChanged)
		self.selectionChanged()

	def removeWatcher(self):
		if self.selectionChanged in self.parent["config"].onSelectionChanged:
			self.parent["config"].onSelectionChanged.remove(self.selectionChanged)

	def selectionChanged(self):
		self["entry"].text = item[0][0] if (item := (self.parent["config"].getCurrent())) else ""


def PluginMain(session, **kwargs):
	return session.open(PluginSetup)

def Plugins(**kwargs):
	return [PluginDescriptor(name="iptv-org playlists", description=_("Make IPTV bouquets based on m3u playlists from github.com/iptv-org"), where=PluginDescriptor.WHERE_PLUGINMENU, needsRestart=True, fnc=PluginMain)]
