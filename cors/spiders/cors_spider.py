from datetime import date
import re
from itertools import izip_longest

from scrapy.contrib.spiders import CrawlSpider, Rule
from scrapy.contrib.linkextractors.sgml import SgmlLinkExtractor
from scrapy.selector import HtmlXPathSelector

from cors.items import CorsItem

def clean(text):
	"""Removes \r and \n from text,
	Converts unicode entities back to html/xml ones
	"""
	return u' '.join([w.strip() for w in text.split()]).encode('ascii', 'xmlcharrefreplace')

def process_exam_date(exam):
	"""Processes an exam date and returns a dict representation for saving to mongodb
	:param exam string
	:returns {date: <<date in ISO8601 format>>, time (no standard): <<AM or PM or EVENING>>}
	If there's an index error at any stage, this returns the original string
	"""
	try:
		t = exam.split()
		d = t[0].split('-')
		return {'date': date(int(d[2]), int(d[1]), int(d[0])).isoformat(), 'time': t[1]}
	except IndexError:
		return exam

def convert_day(day):
	"""Takes day as scraped, and converts it to number representations
	"""
	mapping = {
		'MONDAY': 1,
		'TUESDAY': 2,
		'WEDNESDAY': 3,
		'THURSDAY': 4,
		'FRIDAY': 5,
		'SATURDAY': 6,
		'SUNDAY': 7
	}
	return mapping.get(day, None)

def convert_occur(text):
	"""Convert string of occurences to a list of weeks.
	"""
	def_weeks = [1,2,3,4,5,6,7,8,9,10,11,12,13]
	if text == "EVERY WEEK":
		return def_weeks
	elif text == "EVEN WEEK":
		return filter(lambda x: x%2==0, def_weeks)
	elif text == "ODD WEEK":
		return filter(lambda x: x%2!=0, def_weeks)
	else:
		return [int(e) for e in text.split(',')]

def timeparse(parselist):
	"""The hairiest piece of parsing code you'll find here (sorry)
	This takes a list of parsed strings, like:

		[u'DESIGN LECTURE Class [1]',
		u'MONDAY From 1400 hrs to 1800 hrs in AKI2,',
		u'Week(s): EVERY WEEK.',
		u'THURSDAY From 1400 hrs to 1800 hrs in AKI2,',
		u'Week(s): EVERY WEEK.',
		u'Not Available in Tutorial Balloting yet.'],

	and turns it into this:

		[
			{
			'name': DESIGN LECTURE Class [1],
			'sessions':
					[{
						'day': 1,
						'starttime': '1400',
						'endtime': '1800',
						'location': 'AK12',
						'occurence': [1,2,3,4,5,6,7,8,9,10,11,12,13]
					}]
			}
		]

		There can be many lecture classes in the list. There can also be
		multiple sessions, which explains why the {day, starttime, endtime etc}
		dict is stored in a list.
	"""
	time = re.compile('(?P<day>\w+) From (?P<starttime>\d+) hrs to (?P<endtime>\d+) hrs in (?P<location>.+),')
	occur = re.compile('Week\(s\): (.*?)\.')
	ballot = re.compile('.*? Tutorial Balloting .*?')
	nolecture = re.compile('.*? no lectures .*?')

	res = []
	pos = 0 # pos indicates tutorial or lecture position in the list
	secondary = 0 # secondary indicates session number, for classes with multiple sessions

	for l in parselist:
		time_re = time.match(l)
		occur_re = occur.match(l)
		ballot_re = ballot.match(l)

		# There are no lectures.
		if nolecture.match(l):
			return u'null'

		if not time_re and not occur_re and not ballot_re:
			# End and start conditions
			# res is empty
			if pos == 0 and not res:
				res.append({'name': l})
			# res is already populated
			# then we know this is a secondary lesson slot
			else:
				res.append({'name': l})
				secondary = 0
				pos = pos+1

		if time_re:
			day = time_re.group('day')
			starttime = time_re.group('starttime')
			endtime = time_re.group('endtime')
			location = time_re.group('location')

			curr_session = {
						'day': convert_day(day),
						'starttime': starttime,
						'endtime': endtime,
						'location': location
						}
			# if this is the first session
			if secondary == 0:
				res[pos]['sessions'] = [curr_session]
			else:
				res[pos]['sessions'].append(curr_session)

		if occur_re:
			occurence = convert_occur(occur_re.group(1)) # occur_re.group(1) is the grouped regex as above
			# No, I am not kidding you. This is as ugly as they come.
			res[pos]['sessions'][secondary]['occurence'] = occurence
			secondary = secondary + 1 # indicate session number
	return res


class CorsSpider(CrawlSpider):
	"""Spider class, used by Scrapy.
	"""
	name = "cors"
	allowed_domains = ["nus.edu.sg"]
	start_urls = [
		"https://aces01.nus.edu.sg/cors/jsp/report/ModuleInfoListing.jsp",
        "https://aces01.nus.edu.sg/cors/jsp/report/GEMInfoListing.jsp",
        "https://aces01.nus.edu.sg/cors/jsp/report/SSMInfoListing.jsp"
	]

	# Follow links that contain the following rule (in this case to individual module pages)
	rules = (
		Rule(SgmlLinkExtractor(allow=('ModuleDetailedInfo\.jsp', )), callback='parse_module'),
	)

	def parse_module(self, response):
		"""Scrapes each individual module page. Scraped items are passed to pipelines.py,
		where they are processed and save to mongodb
		"""
		hxs = HtmlXPathSelector(response)
		module = hxs.select('id("wrapper")/table/tr[2]/td/table[1]/tr[3]/td/table')

		# XPath selectors
		code = module.select('tr[position()=2]/td[position()=2]/text()').extract()
		name = module.select('tr[position()=3]/td[position()=2]/text()').extract()
		desc = module.select('tr[position()=4]/td[position()=2]/text()').extract()
		mc = module.select('tr[position()=7]/td[position()=2]/text()').extract()
		lecture = module.select('tr[position()=2]/td/div/table/tr/td/text()').extract()
		exam = module.select('tr[position()=6]/td[position()=2]/text()').extract()
		prereq = module.select('tr[position()=8]/td[position()=2]/text()').extract()
		preclu = module.select('tr[position()=9]/td[position()=2]/text()').extract()
		workload = module.select('tr[position()=10]/td[position()=2]/text()').extract()

		tutorials = hxs.select('id("wrapper")/table/tr[2]/td/table[1]/tr[3]/td/table[4]/tr[3]/td/div/table[position()>0]/tr/td/text()').extract()

		# encode exam date to ISO8601
		exam = exam[0].strip() if exam else u'null'
		if exam != "No Exam Date.":
			exam = process_exam_date(exam)
		else:
			exam = u'null'

		item = CorsItem()

		# strip the strings in lecture and tutorails
		lecture = [w.strip() for w in lecture]
		tutorials = [t.strip() for t in tutorials]

		item['code'] = ' '.join(code[0].split()) if code else u'null'
		item['name'] = name[0].strip() if name else u'null'
		item['desc'] = clean(desc[0]) if desc else u'null'
		item['mc'] = mc[0].strip() if mc else u'null'
		item['lecture_time_table'] = timeparse(lecture) if lecture else u'null'
		item['tutorial_time_table'] = timeparse(tutorials) if tutorials else u'null'
		item['exam'] = exam
		item['prerequisite'] = clean(prereq[0]) if prereq else u'null'
		item['preclusion'] = clean(preclu[0]) if preclu else u'null'
		item['workload'] = workload[0].strip() if workload else u'null'

		return item
