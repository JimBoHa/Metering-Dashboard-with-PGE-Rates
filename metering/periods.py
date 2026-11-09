"""Calendar-month billing boundaries; end months are exclusive."""
import re
from datetime import date

PGE_ZONE = "America/Los_Angeles"


def month_date(value):
    if not isinstance(value,str) or not re.fullmatch(r"\d{4}-\d{2}",value):
        raise ValueError("Use a month in YYYY-MM format")
    try:return date.fromisoformat(value+"-01")
    except ValueError:raise ValueError("Choose a valid calendar month") from None


def next_month(day):
    return date(day.year+day.month//12,day.month%12+1,1)


def months_between(start,end):
    a,b=month_date(start),month_date(end)
    count=(b.year-a.year)*12+b.month-a.month
    if not 0<count<=36:raise ValueError("Choose an increasing range of at most 36 months; the end month is exclusive")
    result=[]
    while a<b:
        following=next_month(a);result.append((a,following));a=following
    return result
