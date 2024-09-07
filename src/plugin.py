# for localized messages
from . import _, group_titles

from os import makedirs as os_makedirs, path as os_path
from requests import get, exceptions
from shutil import rmtree
from time import time
import pickle

from enigma import eDVBDB, eTimer

from Components.ActionMap import ActionMap
from Components.config import config, ConfigSubsection, ConfigSelection, ConfigText, configfile
from Components.SelectionList import SelectionList, SelectionEntryComponent
from Components.Sources.StaticText import StaticText
from Plugins.Plugin import PluginDescriptor
from Screens.ChoiceBox import ChoiceBox
from Screens.MessageBox import MessageBox
from Screens.Screen import Screen, ScreenSummary
from Tools.Directories import sanitizeFilename


config.plugins.iptv_org = ConfigSubsection()
choices = {"genre": _("genre"), "language": _("language"), "country": _("country")}
config.plugins.iptv_org.current = ConfigSelection(choices=[(x[0], x[1]) for x in choices.items()], default=list(choices.keys())[0])
for choice in choices:
	setattr(config.plugins.iptv_org, choice, ConfigText("", False))


class Fetcher():
	def __init__(self):
		self.tempDir = "/tmp/iptv-org"
		os_makedirs(self.tempDir, exist_ok=True)
		self.cachefile = "/tmp/iptv-org.cache"
		self.playlists = {"country": "https://iptv-org.github.io/iptv/index.country.m3u", "genre": "https://iptv-org.github.io/iptv/index.category.m3u", "language": "https://iptv-org.github.io/iptv/index.language.m3u"}
		self.bouquetFilename = "userbouquet.iptv-org.%s.tv"
		self.bouquetName = _("iptv-org")
		self.playlists_processed = {key: {} for key in self.playlists.keys()}
		self.cache_updated = False
		if os_path.exists(self.cachefile):
			try:
				mtime = os_path.getmtime(self.cachefile)
				if mtime < time() - 86400:  # if file is older than one day delete it
					os_remove(self.cachefile)
				else:
					with open(self.cachefile, 'rb') as cache_input:
						self.playlists_processed = pickle.load(cache_input)
			except Exception as e:
				print("[iptv-org plugin] failed to open cache file", e)

	def downloadPage(self):
		os_makedirs(self.tempDir, exist_ok=True)
		link = self.playlists[config.plugins.iptv_org.current.value]
		try:
			response = get(link, timeout=2.50)
			response.raise_for_status()
			with open(self.tempDir + "/" + config.plugins.iptv_org.current.value, "wb") as f:
				f.write(response.content)
		except exceptions.RequestException as error:
			print("[iptv-org plugin] failed to download", link)

	def getPlaylist(self):
		current = self.playlists_processed[config.plugins.iptv_org.current.value]
		if not current:
			self.downloadPage()
			known_urls = []
			group_title = ""
			channelname = ""
			url = ""
			with open(self.tempDir + "/" + config.plugins.iptv_org.current.value, encoding='utf-8', errors="ignore") as f:
				for line in f:
					if line.startswith("#EXTINF:"):
						group_title = ""
						channelname = ""
						url = ""
						if len(line_split := line.rsplit(",", 1)) > 1:
							channelname = line_split[1].strip()  # .rsplit("(", 1)[0].strip()
							if len(line_split2 := line_split[0].split('group-title="', 1)) > 1:
								group_title = line_split2[1].split('"', 1)[0].strip()
					elif line.startswith("http"):
						url = line.strip()
					if channelname and group_title and url and url not in known_urls:
						if group_title not in current:
							current[group_title] = []
						current[group_title].append((channelname, url))
						known_urls.append(url)
						group_title = ""
						channelname = ""
						url = ""
				self.cache_updated = True

	def createBouquet(self, enabled):
		current = self.playlists_processed[config.plugins.iptv_org.current.value]
		for group_title in sorted([k for k in current.keys() if k in enabled], key=lambda x: group_titles.get(x, x).lower()):
			bouquet_list = []
			if current[group_title]:  # group_title not empty (how could it be)
				bouquet_list.append("1:64:0:0:0:0:0:0:0:0:%s" % group_titles.get(group_title, group_title))
				for channelname, url in sorted(current[group_title]):
					bouquet_list.append("4097:0:1:1:1:1:CCCC0000:0:0:0:%s:%s" % (url.replace(":", "%3a"), channelname))
			if bouquet_list:
				eDVBDB.getInstance().addOrUpdateBouquet(self.bouquetName + " - " + group_titles.get(group_title, group_title), self.bouquetFilename % sanitizeFilename(group_title).replace(" ", "_").strip().lower(), bouquet_list, False)

	def cleanup(self):
		rmtree(self.tempDir)
		if self.cache_updated:
			with open(self.cachefile, 'wb') as cache_output:
				pickle.dump(self.playlists_processed, cache_output, pickle.HIGHEST_PROTOCOL)


class PluginSetup(Screen):
	def __init__(self, session):
		Screen.__init__(self, session)
		self.title = _("iptv-org playlists") + " - " + choices.get(config.plugins.iptv_org.current.value, config.plugins.iptv_org.current.value).title()
		self.skinName = ["Setup"]
		self.enabled = []
		self.options = []
		self.fetcher = Fetcher()
		self.keyBlueText = _("Change category")
		self["config"] = SelectionList([], enableWrapAround=True)
		self["key_red"] = StaticText(_("Cancel"))
		self["key_green"] = StaticText(_("Create bouquets"))
		self["key_yellow"] = StaticText(_("Toggle all"))
		self["key_blue"] = StaticText(self.keyBlueText)
		self["description"] = StaticText("")
		self["actions"] = ActionMap(["SetupActions", "ColorActions"],
		{
			"ok": self["config"].toggleSelection,
			"save": self.keyCreate,
			"cancel": self.keyCancel,
			"yellow": self["config"].toggleAllSelection,
			"blue": self.keyCategory,
		}, -2)
		self.loading_message = _("Downloading playlist - Please wait!")
		self["description"].text = self.loading_message
		self.onClose.append(self.__onClose)
		self.timer = eTimer()
		self.timer.callback.append(self.buildList)
		self.timer.start(10, 1)

	def __onClose(self):
		self.fetcher.cleanup()

	def buildList(self):
		self["actions"].setEnabled(False)
		self.fetcher.getPlaylist()  # get playlist is not already local              group_titles.get(x, x)
		self.options = sorted(list(self.fetcher.playlists_processed[config.plugins.iptv_org.current.value].keys()), key=lambda x: group_titles.get(x, x).lower())
		self.enabled = [x for x in getattr(config.plugins.iptv_org, config.plugins.iptv_org.current.value).value.split("|") if x in self.options]
		self["config"].setList([SelectionEntryComponent(group_titles.get(x, x), x, "", x in self.enabled) for x in self.options])
		self["actions"].setEnabled(True)
		self["description"].text = ""

	def readList(self):
		self.enabled = [x[0][1] for x in self["config"].list if x[0][3]]
		getattr(config.plugins.iptv_org, config.plugins.iptv_org.current.value).value = "|".join(self.enabled)

	def keyCreate(self):
		self.readList()
		if self.enabled:
			self["actions"].setEnabled(False)
			self.title += " - " + _("Creating bouquets")
			self["description"].text = _("Creating bouquets. This may take some time. Please be patient.")
			self["key_red"].text = ""
			self["key_green"].text = ""
			self["key_yellow"].text = ""
			self["key_blue"].text = ""
			self["config"].setList([])
			config.plugins.iptv_org.current.save()
			for choice in choices:
				getattr(config.plugins.iptv_org, choice).save()
			configfile.save()
			self.runtimer = eTimer()
			self.runtimer.callback.append(self.doRun)
			self.runtimer.start(10, 1)
		else:
			self.session.open(MessageBox, _("Please select the bouquets you wish to create"))

	def doRun(self):
		self.fetcher.createBouquet(self.enabled)
		self.close()

	def keyCancel(self):
		self.readList()
		if any([getattr(config.plugins.iptv_org, choice).isChanged() for choice in choices]):
			self.session.openWithCallback(self.cancelConfirm, MessageBox, _("Really close without saving settings?"))
		else:
			self.close()

	def keyCategory(self):
		current = config.plugins.iptv_org.current
		self.session.openWithCallback(
			self.keyCategoryCallback, ChoiceBox, title=self.keyBlueText,
			list=list(zip(current.description, current.choices)),
			selection=current.getIndex(),
			keys=[]
		)

	def keyCategoryCallback(self, answer):
		if answer:
			config.plugins.iptv_org.current.value = answer[1]
			self.title = _("iptv-org playlists") + " - " + choices.get(config.plugins.iptv_org.current.value, config.plugins.iptv_org.current.value).title()
			self["description"].text = self.loading_message
			self["config"].setList([])
			self.timer.start(10, 1)

	def cancelConfirm(self, result):
		if not result:
			return
		config.plugins.iptv_org.current.cancel()
		for choice in choices:
			getattr(config.plugins.iptv_org, choice).cancel()
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
