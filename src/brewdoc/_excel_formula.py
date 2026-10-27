"""Passive Excel formula metadata and token decoding for BIFF8 and BIFF12."""

from __future__ import annotations

import math
import struct

# Ftab metadata adapted from calamine 0.36.0, Copyright 2016-2026 Johann Tuffe.
# MIT license text is retained in _vendor/THIRD_PARTY_NOTICES.txt.
FTAB = (
    'COUNT', 'IF', 'ISNA', 'ISERROR', 'SUM', 'AVERAGE',
    'MIN', 'MAX', 'ROW', 'COLUMN', 'NA', 'NPV',
    'STDEV', 'DOLLAR', 'FIXED', 'SIN', 'COS', 'TAN',
    'ATAN', 'PI', 'SQRT', 'EXP', 'LN', 'LOG10',
    'ABS', 'INT', 'SIGN', 'ROUND', 'LOOKUP', 'INDEX',
    'REPT', 'MID', 'LEN', 'VALUE', 'TRUE', 'FALSE',
    'AND', 'OR', 'NOT', 'MOD', 'DCOUNT', 'DSUM',
    'DAVERAGE', 'DMIN', 'DMAX', 'DSTDEV', 'VAR', 'DVAR',
    'TEXT', 'LINEST', 'TREND', 'LOGEST', 'GROWTH', 'GOTO',
    'HALT', 'RETURN', 'PV', 'FV', 'NPER', 'PMT',
    'RATE', 'MIRR', 'IRR', 'RAND', 'MATCH', 'DATE',
    'TIME', 'DAY', 'MONTH', 'YEAR', 'WEEKDAY', 'HOUR',
    'MINUTE', 'SECOND', 'NOW', 'AREAS', 'ROWS', 'COLUMNS',
    'OFFSET', 'ABSREF', 'RELREF', 'ARGUMENT', 'SEARCH', 'TRANSPOSE',
    'ERROR', 'STEP', 'TYPE', 'ECHO', 'SET.NAME', 'CALLER',
    'DEREF', 'WINDOWS', 'SERIES', 'DOCUMENTS', 'ACTIVE.CELL', 'SELECTION',
    'RESULT', 'ATAN2', 'ASIN', 'ACOS', 'CHOOSE', 'HLOOKUP',
    'VLOOKUP', 'LINKS', 'INPUT', 'ISREF', 'GET.FORMULA', 'GET.NAME',
    'SET.VALUE', 'LOG', 'EXEC', 'CHAR', 'LOWER', 'UPPER',
    'PROPER', 'LEFT', 'RIGHT', 'EXACT', 'TRIM', 'REPLACE',
    'SUBSTITUTE', 'CODE', 'NAMES', 'DIRECTORY', 'FIND', 'CELL',
    'ISERR', 'ISTEXT', 'ISNUMBER', 'ISBLANK', 'T', 'N',
    'FOPEN', 'FCLOSE', 'FSIZE', 'FREADLN', 'FREAD', 'FWRITELN',
    'FWRITE', 'FPOS', 'DATEVALUE', 'TIMEVALUE', 'SLN', 'SYD',
    'DDB', 'GET.DEF', 'REFTEXT', 'TEXTREF', 'INDIRECT', 'REGISTER',
    'CALL', 'ADD.BAR', 'ADD.MENU', 'ADD.COMMAND', 'ENABLE.COMMAND', 'CHECK.COMMAND',
    'RENAME.COMMAND', 'SHOW.BAR', 'DELETE.MENU', 'DELETE.COMMAND', 'GET.CHART.ITEM', 'DIALOG.BOX',
    'CLEAN', 'MDETERM', 'MINVERSE', 'MMULT', 'FILES', 'IPMT',
    'PPMT', 'COUNTA', 'CANCEL.KEY', 'FOR', 'WHILE', 'BREAK',
    'NEXT', 'INITIATE', 'REQUEST', 'POKE', 'EXECUTE', 'TERMINATE',
    'RESTART', 'HELP', 'GET.BAR', 'PRODUCT', 'FACT', 'GET.CELL',
    'GET.WORKSPACE', 'GET.WINDOW', 'GET.DOCUMENT', 'DPRODUCT', 'ISNONTEXT', 'GET.NOTE',
    'NOTE', 'STDEVP', 'VARP', 'DSTDEVP', 'DVARP', 'TRUNC',
    'ISLOGICAL', 'DCOUNTA', 'DELETE.BAR', 'UNREGISTER', '', '',
    'USDOLLAR', 'FINDB', 'SEARCHB', 'REPLACEB', 'LEFTB', 'RIGHTB',
    'MIDB', 'LENB', 'ROUNDUP', 'ROUNDDOWN', 'ASC', 'DBCS',
    'RANK', '', '', 'ADDRESS', 'DAYS360', 'TODAY',
    'VDB', 'ELSE', 'ELSE.IF', 'END.IF', 'FOR.CELL', 'MEDIAN',
    'SUMPRODUCT', 'SINH', 'COSH', 'TANH', 'ASINH', 'ACOSH',
    'ATANH', 'DGET', 'CREATE.OBJECT', 'VOLATILE', 'LAST.ERROR', 'CUSTOM.UNDO',
    'CUSTOM.REPEAT', 'FORMULA.CONVERT', 'GET.LINK.INFO', 'TEXT.BOX', 'INFO', 'GROUP',
    'GET.OBJECT', 'DB', 'PAUSE', '', '', 'RESUME',
    'FREQUENCY', 'ADD.TOOLBAR', 'DELETE.TOOLBAR', 'User', 'RESET.TOOLBAR', 'EVALUATE',
    'GET.TOOLBAR', 'GET.TOOL', 'SPELLING.CHECK', 'ERROR.TYPE', 'APP.TITLE', 'WINDOW.TITLE',
    'SAVE.TOOLBAR', 'ENABLE.TOOL', 'PRESS.TOOL', 'REGISTER.ID', 'GET.WORKBOOK', 'AVEDEV',
    'BETADIST', 'GAMMALN', 'BETAINV', 'BINOMDIST', 'CHIDIST', 'CHIINV',
    'COMBIN', 'CONFIDENCE', 'CRITBINOM', 'EVEN', 'EXPONDIST', 'FDIST',
    'FINV', 'FISHER', 'FISHERINV', 'FLOOR', 'GAMMADIST', 'GAMMAINV',
    'CEILING', 'HYPGEOMDIST', 'LOGNORMDIST', 'LOGINV', 'NEGBINOMDIST', 'NORMDIST',
    'NORMSDIST', 'NORMINV', 'NORMSINV', 'STANDARDIZE', 'ODD', 'PERMUT',
    'POISSON', 'TDIST', 'WEIBULL', 'SUMXMY2', 'SUMX2MY2', 'SUMX2PY2',
    'CHITEST', 'CORREL', 'COVAR', 'FORECAST', 'FTEST', 'INTERCEPT',
    'PEARSON', 'RSQ', 'STEYX', 'SLOPE', 'TTEST', 'PROB',
    'DEVSQ', 'GEOMEAN', 'HARMEAN', 'SUMSQ', 'KURT', 'SKEW',
    'ZTEST', 'LARGE', 'SMALL', 'QUARTILE', 'PERCENTILE', 'PERCENTRANK',
    'MODE', 'TRIMMEAN', 'TINV', '', 'MOVIE.COMMAND', 'GET.MOVIE',
    'CONCATENATE', 'POWER', 'PIVOT.ADD.DATA', 'GET.PIVOT.TABLE', 'GET.PIVOT.FIELD', 'GET.PIVOT.ITEM',
    'RADIANS', 'DEGREES', 'SUBTOTAL', 'SUMIF', 'COUNTIF', 'COUNTBLANK',
    'SCENARIO.GET', 'OPTIONS.LISTS.GET', 'ISPMT', 'DATEDIF', 'DATESTRING', 'NUMBERSTRING',
    'ROMAN', 'OPEN.DIALOG', 'SAVE.DIALOG', 'VIEW.GET', 'GETPIVOTDATA', 'HYPERLINK',
    'PHONETIC', 'AVERAGEA', 'MAXA', 'MINA', 'STDEVPA', 'VARPA',
    'STDEVA', 'VARA', 'BAHTTEXT', 'THAIDAYOFWEEK', 'THAIDIGIT', 'THAIMONTHOFYEAR',
    'THAINUMSOUND', 'THAINUMSTRING', 'THAISTRINGLENGTH', 'ISTHAIDIGIT', 'ROUNDBAHTDOWN', 'ROUNDBAHTUP',
    'THAIYEAR', 'RTD', 'CUBEVALUE', 'CUBEMEMBER', 'CUBEMEMBERPROPERTY', 'CUBERANKEDMEMBER',
    'HEX2BIN', 'HEX2DEC', 'HEX2OCT', 'DEC2BIN', 'DEC2HEX', 'DEC2OCT',
    'OCT2BIN', 'OCT2HEX', 'OCT2DEC', 'BIN2DEC', 'BIN2OCT', 'BIN2HEX',
    'IMSUB', 'IMDIV', 'IMPOWER', 'IMABS', 'IMSQRT', 'IMLN',
    'IMLOG2', 'IMLOG10', 'IMSIN', 'IMCOS', 'IMEXP', 'IMARGUMENT',
    'IMCONJUGATE', 'IMAGINARY', 'IMREAL', 'COMPLEX', 'IMSUM', 'IMPRODUCT',
    'SERIESSUM', 'FACTDOUBLE', 'SQRTPI', 'QUOTIENT', 'DELTA', 'GESTEP',
    'ISEVEN', 'ISODD', 'MROUND', 'ERF', 'ERFC', 'BESSELJ',
    'BESSELK', 'BESSELY', 'BESSELI', 'XIRR', 'XNPV', 'PRICEMAT',
    'YIELDMAT', 'INTRATE', 'RECEIVED', 'DISC', 'PRICEDISC', 'YIELDDISC',
    'TBILLEQ', 'TBILLPRICE', 'TBILLYIELD', 'PRICE', 'YIELD', 'DOLLARDE',
    'DOLLARFR', 'NOMINAL', 'EFFECT', 'CUMPRINC', 'CUMIPMT', 'EDATE',
    'EOMONTH', 'YEARFRAC', 'COUPDAYBS', 'COUPDAYS', 'COUPDAYSNC', 'COUPNCD',
    'COUPNUM', 'COUPPCD', 'DURATION', 'MDURATION', 'ODDLPRICE', 'ODDLYIELD',
    'ODDFPRICE', 'ODDFYIELD', 'RANDBETWEEN', 'WEEKNUM', 'AMORDEGRC', 'AMORLINC',
    'CONVERT', 'ACCRINT', 'ACCRINTM', 'WORKDAY', 'NETWORKDAYS', 'GCD',
    'MULTINOMIAL', 'LCM', 'FVSCHEDULE', 'CUBEKPIMEMBER', 'CUBESET', 'CUBESETCOUNT',
    'IFERROR', 'COUNTIFS', 'SUMIFS', 'AVERAGEIF', 'AVERAGEIFS',
)
FTAB_ARGC = (
    255, 3, 1, 1, 255, 255, 255, 255, 1, 1, 0, 254, 255, 2, 3, 1, 1, 1, 1, 0,
    1, 1, 1, 1, 1, 1, 1, 2, 3, 4, 2, 3, 1, 1, 0, 0, 255, 255, 1, 2,
    3, 3, 3, 3, 3, 3, 255, 3, 2, 4, 4, 4, 4, 1, 1, 1, 5, 5, 5, 5,
    6, 3, 2, 0, 3, 3, 3, 1, 1, 1, 2, 1, 1, 1, 0, 1, 1, 1, 5, 2,
    2, 3, 3, 1, 2, 0, 1, 1, 2, 0, 1, 2, 2, 2, 0, 0, 1, 2, 1, 1,
    255, 4, 4, 2, 7, 1, 1, 2, 2, 2, 4, 1, 1, 1, 1, 2, 2, 2, 1, 4,
    4, 1, 3, 1, 3, 2, 1, 1, 1, 1, 1, 1, 2, 1, 1, 1, 2, 2, 2, 2,
    1, 1, 3, 4, 5, 3, 2, 2, 2, 255, 255, 1, 4, 5, 5, 5, 5, 1, 3, 4,
    3, 1, 1, 1, 1, 1, 2, 6, 6, 255, 2, 4, 1, 0, 0, 2, 2, 3, 2, 1,
    1, 1, 4, 255, 1, 2, 1, 2, 2, 3, 1, 3, 4, 255, 255, 3, 3, 2, 1, 3,
    1, 1, 0, 0, 2, 3, 3, 4, 2, 2, 3, 3, 2, 2, 1, 1, 3, 0, 0, 5,
    3, 0, 7, 0, 1, 0, 3, 255, 255, 1, 1, 1, 1, 1, 1, 3, 11, 1, 0, 2,
    3, 5, 4, 4, 1, 0, 5, 5, 1, 0, 0, 1, 2, 2, 1, 255, 1, 1, 2, 3,
    3, 1, 1, 1, 2, 3, 3, 3, 2, 255, 5, 1, 5, 4, 2, 2, 2, 3, 3, 1,
    3, 3, 3, 1, 1, 2, 4, 3, 2, 4, 3, 3, 3, 4, 1, 3, 1, 3, 1, 2,
    3, 3, 4, 2, 2, 2, 2, 2, 2, 3, 2, 2, 2, 2, 2, 2, 4, 4, 255, 255,
    255, 255, 255, 255, 3, 2, 2, 2, 2, 3, 255, 2, 2, 4, 4, 3, 255, 2, 9, 2,
    3, 4, 1, 1, 255, 3, 2, 1, 2, 1, 4, 3, 1, 2, 2, 4, 5, 2, 128, 2,
    1, 255, 255, 255, 255, 255, 255, 255, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 255,
    255, 3, 3, 4, 2, 1, 2, 2, 2, 2, 2, 2, 1, 1, 2, 2, 2, 2, 2, 1,
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 3, 255, 255, 4, 1, 1, 2, 2, 2,
    1, 1, 2, 2, 1, 2, 2, 2, 2, 3, 3, 6, 6, 5, 5, 5, 5, 5, 3, 3,
    3, 7, 7, 2, 2, 2, 2, 6, 6, 2, 2, 3, 4, 4, 4, 4, 4, 4, 6, 6,
    8, 8, 8, 8, 2, 2, 7, 7, 8, 8, 5, 3, 3, 255, 255, 255, 2, 4, 5, 1,
    2, 128, 129, 3, 129,
)

# MS-XLSB 2.5.98.5 Cetab: command ID, name and minimum/maximum argument counts.
# MAIL.LOGON has a truncated argument grammar in the published table.
CETAB = {
    0x0000: ('BEEP', 0, 1),
    0x0001: ('OPEN', 0, 17),
    0x0002: ('OPEN.LINKS', 0, 15),
    0x0003: ('CLOSE.ALL', 0, 0),
    0x0004: ('SAVE', 0, 0),
    0x0005: ('SAVE.AS', 0, 7),
    0x0006: ('FILE.DELETE', 0, 1),
    0x0007: ('PAGE.SETUP', 0, 30),
    0x0008: ('PRINT', 0, 17),
    0x0009: ('PRINTER.SETUP', 0, 1),
    0x000A: ('QUIT', 0, 0),
    0x000B: ('NEW.WINDOW', 0, 0),
    0x000C: ('ARRANGE.ALL', 0, 4),
    0x000D: ('WINDOW.SIZE', 0, 3),
    0x000E: ('WINDOW.MOVE', 0, 3),
    0x000F: ('FULL', 0, 1),
    0x0010: ('CLOSE', 0, 2),
    0x0011: ('RUN', 0, 2),
    0x0016: ('SET.PRINT.AREA', 0, 1),
    0x0017: ('SET.PRINT.TITLES', 0, 2),
    0x0018: ('SET.PAGE.BREAK', 0, 0),
    0x0019: ('REMOVE.PAGE.BREAK', 0, 2),
    0x001A: ('FONT', 0, 2),
    0x001B: ('DISPLAY', 0, 9),
    0x001C: ('PROTECT.DOCUMENT', 0, 7),
    0x001D: ('PRECISION', 0, 1),
    0x001E: ('A1.R1C1', 0, 1),
    0x001F: ('CALCULATE.NOW', 0, 0),
    0x0020: ('CALCULATION', 0, 11),
    0x0022: ('DATA.FIND', 0, 1),
    0x0023: ('EXTRACT', 0, 1),
    0x0024: ('DATA.DELETE', 0, 0),
    0x0025: ('SET.DATABASE', 0, 0),
    0x0026: ('SET.CRITERIA', 0, 0),
    0x0027: ('SORT', 0, 17),
    0x0028: ('DATA.SERIES', 0, 6),
    0x0029: ('TABLE', 0, 2),
    0x002A: ('FORMAT.NUMBER', 0, 1),
    0x002B: ('ALIGNMENT', 0, 10),
    0x002C: ('STYLE', 0, 2),
    0x002D: ('BORDER', 0, 27),
    0x002E: ('CELL.PROTECTION', 0, 2),
    0x002F: ('COLUMN.WIDTH', 0, 5),
    0x0030: ('UNDO', 0, 0),
    0x0031: ('CUT', 0, 2),
    0x0032: ('COPY', 0, 2),
    0x0033: ('PASTE', 0, 1),
    0x0034: ('CLEAR', 0, 1),
    0x0035: ('PASTE.SPECIAL', 0, 7),
    0x0036: ('EDIT.DELETE', 0, 1),
    0x0037: ('INSERT', 0, 2),
    0x0038: ('FILL.RIGHT', 0, 0),
    0x0039: ('FILL.DOWN', 0, 0),
    0x003D: ('DEFINE.NAME', 0, 7),
    0x003E: ('CREATE.NAMES', 0, 4),
    0x003F: ('FORMULA.GOTO', 0, 2),
    0x0040: ('FORMULA.FIND', 0, 12),
    0x0041: ('SELECT.LAST.CELL', 0, 0),
    0x0042: ('SHOW.ACTIVE.CELL', 0, 0),
    0x0043: ('GALLERY.AREA', 0, 2),
    0x0044: ('GALLERY.BAR', 0, 2),
    0x0045: ('GALLERY.COLUMN', 0, 2),
    0x0046: ('GALLERY.LINE', 0, 2),
    0x0047: ('GALLERY.PIE', 0, 2),
    0x0048: ('GALLERY.SCATTER', 0, 2),
    0x0049: ('COMBINATION', 0, 1),
    0x004A: ('PREFERRED', 0, 0),
    0x004B: ('ADD.OVERLAY', 0, 0),
    0x004C: ('GRIDLINES', 0, 7),
    0x004D: ('SET.PREFERRED', 0, 1),
    0x004E: ('AXES', 0, 6),
    0x004F: ('LEGEND', 0, 1),
    0x0050: ('ATTACH.TEXT', 0, 3),
    0x0051: ('ADD.ARROW', 0, 0),
    0x0052: ('SELECT.CHART', 0, 0),
    0x0053: ('SELECT.PLOT.AREA', 0, 0),
    0x0054: ('PATTERNS', 0, 13),
    0x0055: ('MAIN.CHART', 0, 10),
    0x0056: ('OVERLAY', 0, 12),
    0x0057: ('SCALE', 0, 10),
    0x0058: ('FORMAT.LEGEND', 0, 1),
    0x0059: ('FORMAT.TEXT', 0, 11),
    0x005A: ('EDIT.REPEAT', 0, 0),
    0x005B: ('PARSE', 0, 2),
    0x005C: ('JUSTIFY', 0, 0),
    0x005D: ('HIDE', 0, 0),
    0x005E: ('UNHIDE', 0, 1),
    0x005F: ('WORKSPACE', 0, 16),
    0x0060: ('FORMULA', 0, 2),
    0x0061: ('FORMULA.FILL', 0, 2),
    0x0062: ('FORMULA.ARRAY', 0, 2),
    0x0063: ('DATA.FIND.NEXT', 0, 0),
    0x0064: ('DATA.FIND.PREV', 0, 0),
    0x0065: ('FORMULA.FIND.NEXT', 0, 0),
    0x0066: ('FORMULA.FIND.PREV', 0, 0),
    0x0067: ('ACTIVATE', 0, 2),
    0x0068: ('ACTIVATE.NEXT', 0, 1),
    0x0069: ('ACTIVATE.PREV', 0, 1),
    0x006A: ('UNLOCKED.NEXT', 0, 0),
    0x006B: ('UNLOCKED.PREV', 0, 0),
    0x006C: ('COPY.PICTURE', 0, 3),
    0x006D: ('SELECT', 0, 2),
    0x006E: ('DELETE.NAME', 0, 1),
    0x006F: ('DELETE.FORMAT', 0, 1),
    0x0070: ('VLINE', 0, 1),
    0x0071: ('HLINE', 0, 1),
    0x0072: ('VPAGE', 0, 1),
    0x0073: ('HPAGE', 0, 1),
    0x0074: ('VSCROLL', 0, 2),
    0x0075: ('HSCROLL', 0, 2),
    0x0076: ('ALERT', 0, 3),
    0x0077: ('NEW', 0, 3),
    0x0078: ('CANCEL.COPY', 0, 1),
    0x0079: ('SHOW.CLIPBOARD', 0, 0),
    0x007A: ('MESSAGE', 0, 2),
    0x007C: ('PASTE.LINK', 0, 0),
    0x007D: ('APP.ACTIVATE', 0, 2),
    0x007E: ('DELETE.ARROW', 0, 0),
    0x007F: ('ROW.HEIGHT', 0, 4),
    0x0080: ('FORMAT.MOVE', 0, 3),
    0x0081: ('FORMAT.SIZE', 0, 3),
    0x0082: ('FORMULA.REPLACE', 0, 11),
    0x0083: ('SEND.KEYS', 0, 2),
    0x0084: ('SELECT.SPECIAL', 0, 3),
    0x0085: ('APPLY.NAMES', 0, 7),
    0x0086: ('REPLACE.FONT', 0, 10),
    0x0087: ('FREEZE.PANES', 0, 3),
    0x0088: ('SHOW.INFO', 0, 1),
    0x0089: ('SPLIT', 0, 2),
    0x008A: ('ON.WINDOW', 0, 2),
    0x008B: ('ON.DATA', 0, 2),
    0x008C: ('DISABLE.INPUT', 0, 1),
    0x008E: ('OUTLINE', 0, 4),
    0x008F: ('LIST.NAMES', 0, 0),
    0x0090: ('FILE.CLOSE', 0, 2),
    0x0091: ('SAVE.WORKBOOK', 0, 6),
    0x0092: ('DATA.FORM', 0, 0),
    0x0093: ('COPY.CHART', 0, 1),
    0x0094: ('ON.TIME', 0, 4),
    0x0095: ('WAIT', 0, 1),
    0x0096: ('FORMAT.FONT', 0, 15),
    0x0097: ('FILL.UP', 0, 0),
    0x0098: ('FILL.LEFT', 0, 0),
    0x0099: ('DELETE.OVERLAY', 0, 0),
    0x009B: ('SHORT.MENUS', 0, 1),
    0x009F: ('SET.UPDATE.STATUS', 0, 3),
    0x00A1: ('COLOR.PALETTE', 0, 1),
    0x00A2: ('DELETE.STYLE', 0, 1),
    0x00A3: ('WINDOW.RESTORE', 0, 1),
    0x00A4: ('WINDOW.MAXIMIZE', 0, 1),
    0x00A6: ('CHANGE.LINK', 0, 3),
    0x00A7: ('CALCULATE.DOCUMENT', 0, 0),
    0x00A8: ('ON.KEY', 0, 2),
    0x00A9: ('APP.RESTORE', 0, 0),
    0x00AA: ('APP.MOVE', 0, 2),
    0x00AB: ('APP.SIZE', 0, 2),
    0x00AC: ('APP.MINIMIZE', 0, 0),
    0x00AD: ('APP.MAXIMIZE', 0, 0),
    0x00AE: ('BRING.TO.FRONT', 0, 0),
    0x00AF: ('SEND.TO.BACK', 0, 0),
    0x00B9: ('MAIN.CHART.TYPE', 0, 1),
    0x00BA: ('OVERLAY.CHART.TYPE', 0, 1),
    0x00BB: ('SELECT.END', 0, 1),
    0x00BC: ('OPEN.MAIL', 0, 2),
    0x00BD: ('SEND.MAIL', 0, 3),
    0x00BE: ('STANDARD.FONT', 0, 9),
    0x00BF: ('CONSOLIDATE', 0, 5),
    0x00C0: ('SORT.SPECIAL', 0, 14),
    0x00C1: ('GALLERY.3D.AREA', 0, 1),
    0x00C2: ('GALLERY.3D.COLUMN', 0, 1),
    0x00C3: ('GALLERY.3D.LINE', 0, 1),
    0x00C4: ('GALLERY.3D.PIE', 0, 1),
    0x00C5: ('VIEW.3D', 0, 6),
    0x00C6: ('GOAL.SEEK', 0, 3),
    0x00C7: ('WORKGROUP', 0, 1),
    0x00C8: ('FILL.GROUP', 0, 1),
    0x00C9: ('UPDATE.LINK', 0, 2),
    0x00CA: ('PROMOTE', 0, 1),
    0x00CB: ('DEMOTE', 0, 1),
    0x00CC: ('SHOW.DETAIL', 0, 4),
    0x00CE: ('UNGROUP', 0, 0),
    0x00CF: ('OBJECT.PROPERTIES', 0, 2),
    0x00D0: ('SAVE.NEW.OBJECT', 0, 1),
    0x00D1: ('SHARE', 0, 0),
    0x00D2: ('SHARE.NAME', 0, 1),
    0x00D3: ('DUPLICATE', 0, 0),
    0x00D4: ('APPLY.STYLE', 0, 1),
    0x00D5: ('ASSIGN.TO.OBJECT', 0, 1),
    0x00D6: ('OBJECT.PROTECTION', 0, 2),
    0x00D7: ('HIDE.OBJECT', 0, 2),
    0x00D8: ('SET.EXTRACT', 0, 0),
    0x00D9: ('CREATE.PUBLISHER', 0, 4),
    0x00DA: ('SUBSCRIBE.TO', 0, 2),
    0x00DB: ('ATTRIBUTES', 0, 2),
    0x00DC: ('SHOW.TOOLBAR', 0, 10),
    0x00DE: ('PRINT.PREVIEW', 0, 1),
    0x00DF: ('EDIT.COLOR', 0, 4),
    0x00E0: ('SHOW.LEVELS', 0, 2),
    0x00E1: ('FORMAT.MAIN', 0, 14),
    0x00E2: ('FORMAT.OVERLAY', 0, 14),
    0x00E3: ('ON.RECALC', 0, 2),
    0x00E4: ('EDIT.SERIES', 0, 7),
    0x00E5: ('DEFINE.STYLE', 0, 14),
    0x00F0: ('LINE.PRINT', 0, 11),
    0x00F3: ('ENTER.DATA', 0, 1),
    0x00F9: ('GALLERY.RADAR', 0, 2),
    0x00FA: ('MERGE.STYLES', 0, 1),
    0x00FB: ('EDITION.OPTIONS', 0, 7),
    0x00FC: ('PASTE.PICTURE', 0, 0),
    0x00FD: ('PASTE.PICTURE.LINK', 0, 0),
    0x00FE: ('SPELLING', 0, 6),
    0x0100: ('ZOOM', 0, 1),
    0x0103: ('INSERT.OBJECT', 0, 13),
    0x0104: ('WINDOW.MINIMIZE', 0, 1),
    0x0109: ('SOUND.NOTE', 0, 3),
    0x010A: ('SOUND.PLAY', 0, 3),
    0x010B: ('FORMAT.SHAPE', 0, 5),
    0x010C: ('EXTEND.POLYGON', 0, 1),
    0x010D: ('FORMAT.AUTO', 0, 7),
    0x0110: ('GALLERY.3D.BAR', 0, 1),
    0x0111: ('GALLERY.3D.SURFACE', 0, 1),
    0x0112: ('FILL.AUTO', 0, 2),
    0x0114: ('CUSTOMIZE.TOOLBAR', 0, 1),
    0x0115: ('ADD.TOOL', 0, 3),
    0x0116: ('EDIT.OBJECT', 0, 1),
    0x0117: ('ON.DOUBLECLICK', 0, 2),
    0x0118: ('ON.ENTRY', 0, 2),
    0x0119: ('WORKBOOK.ADD', 0, 3),
    0x011A: ('WORKBOOK.MOVE', 0, 3),
    0x011B: ('WORKBOOK.COPY', 0, 3),
    0x011C: ('WORKBOOK.OPTIONS', 0, 3),
    0x011D: ('SAVE.WORKSPACE', 0, 1),
    0x0120: ('CHART.WIZARD', 0, 14),
    0x0121: ('DELETE.TOOL', 0, 2),
    0x0122: ('MOVE.TOOL', 0, 6),
    0x0123: ('WORKBOOK.SELECT', 0, 3),
    0x0124: ('WORKBOOK.ACTIVATE', 0, 2),
    0x0125: ('ASSIGN.TO.TOOL', 0, 3),
    0x0127: ('COPY.TOOL', 0, 2),
    0x0128: ('RESET.TOOL', 0, 2),
    0x0129: ('CONSTRAIN.NUMERIC', 0, 1),
    0x012A: ('PASTE.TOOL', 0, 2),
    0x012E: ('WORKBOOK.NEW', 0, 3),
    0x0131: ('SCENARIO.CELLS', 0, 1),
    0x0132: ('SCENARIO.DELETE', 0, 1),
    0x0133: ('SCENARIO.ADD', 0, 6),
    0x0134: ('SCENARIO.EDIT', 0, 7),
    0x0135: ('SCENARIO.SHOW', 0, 1),
    0x0136: ('SCENARIO.SHOW.NEXT', 0, 0),
    0x0137: ('SCENARIO.SUMMARY', 0, 2),
    0x0138: ('PIVOT.TABLE.WIZARD', 0, 16),
    0x0139: ('PIVOT.FIELD.PROPERTIES', 0, 7),
    0x013A: ('PIVOT.FIELD', 0, 4),
    0x013B: ('PIVOT.ITEM', 0, 4),
    0x013C: ('PIVOT.ADD.FIELDS', 0, 5),
    0x013E: ('OPTIONS.CALCULATION', 0, 10),
    0x013F: ('OPTIONS.EDIT', 0, 11),
    0x0140: ('OPTIONS.VIEW', 0, 18),
    0x0141: ('ADDIN.MANAGER', 0, 3),
    0x0142: ('MENU.EDITOR', 0, 0),
    0x0143: ('ATTACH.TOOLBARS', 0, 0),
    0x0144: ('VBAActivate', 0, 2),
    0x0145: ('OPTIONS.CHART', 0, 3),
    0x0148: ('VBA.INSERT.FILE', 0, 1),
    0x014A: ('VBA.PROCEDURE.DEFINITION', 0, 0),
    0x0150: ('ROUTING.SLIP', 0, 6),
    0x0152: ('ROUTE.DOCUMENT', 0, 0),
    0x0153: ('MAIL.LOGON', 0, None),
    0x0156: ('INSERT.PICTURE', 0, 2),
    0x0157: ('EDIT.TOOL', 0, 2),
    0x0158: ('GALLERY.DOUGHNUT', 0, 2),
    0x015E: ('CHART.TREND', 0, 8),
    0x0160: ('PIVOT.ITEM.PROPERTIES', 0, 7),
    0x0162: ('WORKBOOK.INSERT', 0, 1),
    0x0163: ('OPTIONS.TRANSITION', 0, 5),
    0x0164: ('OPTIONS.GENERAL', 0, 14),
    0x0172: ('FILTER.ADVANCED', 0, 5),
    0x0175: ('MAIL.ADD.MAILER', 0, 0),
    0x0176: ('MAIL.DELETE.MAILER', 0, 0),
    0x0177: ('MAIL.REPLY', 0, 0),
    0x0178: ('MAIL.REPLY.ALL', 0, 0),
    0x0179: ('MAIL.FORWARD', 0, 0),
    0x017A: ('MAIL.NEXT.LETTER', 0, 0),
    0x017B: ('DATA.LABEL', 0, 10),
    0x017C: ('INSERT.TITLE', 0, 5),
    0x017D: ('FONT.PROPERTIES', 0, 14),
    0x017E: ('MACRO.OPTIONS', 0, 10),
    0x017F: ('WORKBOOK.HIDE', 0, 2),
    0x0180: ('WORKBOOK.UNHIDE', 0, 1),
    0x0181: ('WORKBOOK.DELETE', 0, 1),
    0x0182: ('WORKBOOK.NAME', 0, 2),
    0x0184: ('GALLERY.CUSTOM', 0, 1),
    0x0186: ('ADD.CHART.AUTOFORMAT', 0, 2),
    0x0187: ('DELETE.CHART.AUTOFORMAT', 0, 1),
    0x0188: ('CHART.ADD.DATA', 0, 6),
    0x0189: ('AUTO.OUTLINE', 0, 0),
    0x018A: ('TAB.ORDER', 0, 0),
    0x018B: ('SHOW.DIALOG', 0, 1),
    0x018C: ('SELECT.ALL', 0, 0),
    0x018D: ('UNGROUP.SHEETS', 0, 0),
    0x018E: ('SUBTOTAL.CREATE', 0, 6),
    0x018F: ('SUBTOTAL.REMOVE', 0, 0),
    0x0190: ('RENAME.OBJECT', 0, 1),
    0x019C: ('WORKBOOK.SCROLL', 0, 2),
    0x019D: ('WORKBOOK.NEXT', 0, 0),
    0x019E: ('WORKBOOK.PREV', 0, 0),
    0x019F: ('WORKBOOK.TAB.SPLIT', 0, 1),
    0x01A0: ('FULL.SCREEN', 0, 1),
    0x01A1: ('WORKBOOK.PROTECT', 0, 3),
    0x01A4: ('SCROLLBAR.PROPERTIES', 0, 7),
    0x01A5: ('PIVOT.SHOW.PAGES', 0, 2),
    0x01A6: ('TEXT.TO.COLUMNS', 0, 14),
    0x01A7: ('FORMAT.CHARTTYPE', 0, 4),
    0x01A8: ('LINK.FORMAT', 0, 0),
    0x01A9: ('TRACER.DISPLAY', 0, 2),
    0x01AE: ('TRACER.NAVIGATE', 0, 3),
    0x01AF: ('TRACER.CLEAR', 0, 0),
    0x01B0: ('TRACER.ERROR', 0, 0),
    0x01B1: ('PIVOT.FIELD.GROUP', 0, 4),
    0x01B2: ('PIVOT.FIELD.UNGROUP', 0, 0),
    0x01B3: ('CHECKBOX.PROPERTIES', 0, 5),
    0x01B4: ('LABEL.PROPERTIES', 0, 3),
    0x01B5: ('LISTBOX.PROPERTIES', 0, 5),
    0x01B6: ('EDITBOX.PROPERTIES', 0, 4),
    0x01B7: ('PIVOT.REFRESH', 0, 1),
    0x01B8: ('LINK.COMBO', 0, 1),
    0x01B9: ('OPEN.TEXT', 0, 17),
    0x01BA: ('HIDE.DIALOG', 0, 1),
    0x01BB: ('SET.DIALOG.FOCUS', 0, 1),
    0x01BC: ('ENABLE.OBJECT', 0, 2),
    0x01BD: ('PUSHBUTTON.PROPERTIES', 0, 6),
    0x01BE: ('SET.DIALOG.DEFAULT', 0, 1),
    0x01BF: ('FILTER', 0, 6),
    0x01C0: ('FILTER.SHOW.ALL', 0, 0),
    0x01C1: ('CLEAR.OUTLINE', 0, 0),
    0x01C2: ('FUNCTION.WIZARD', 0, 1),
    0x01C3: ('ADD.LIST.ITEM', 0, 2),
    0x01C4: ('SET.LIST.ITEM', 0, 2),
    0x01C5: ('REMOVE.LIST.ITEM', 0, 2),
    0x01C6: ('SELECT.LIST.ITEM', 0, 2),
    0x01C7: ('SET.CONTROL.VALUE', 0, 1),
    0x01C8: ('SAVE.COPY.AS', 0, 1),
    0x01CA: ('OPTIONS.LISTS.ADD', 0, 2),
    0x01CB: ('OPTIONS.LISTS.DELETE', 0, 1),
    0x01CC: ('SERIES.AXES', 0, 1),
    0x01CD: ('SERIES.X', 0, 1),
    0x01CE: ('SERIES.Y', 0, 2),
    0x01CF: ('ERRORBAR.X', 0, 4),
    0x01D0: ('ERRORBAR.Y', 0, 4),
    0x01D1: ('FORMAT.CHART', 0, 18),
    0x01D2: ('SERIES.ORDER', 0, 3),
    0x01D3: ('MAIL.LOGOFF', 0, 0),
    0x01D4: ('CLEAR.ROUTING.SLIP', 0, 1),
    0x01D5: ('APP.ACTIVATE.MICROSOFT', 0, 1),
    0x01D6: ('MAIL.EDIT.MAILER', 0, 6),
    0x01D7: ('ON.SHEET', 0, 3),
    0x01D8: ('STANDARD.WIDTH', 0, 1),
    0x01D9: ('SCENARIO.MERGE', 0, 1),
    0x01DA: ('SUMMARY.INFO', 0, 5),
    0x01DB: ('FIND.FILE', 0, 0),
    0x01DC: ('ACTIVE.CELL.FONT', 0, 14),
    0x01DD: ('ENABLE.TIPWIZARD', 0, 1),
    0x01DE: ('VBA.MAKE.ADDIN', 0, 1),
    0x01E0: ('INSERTDATATABLE', 0, 1),
    0x01E1: ('WORKGROUP.OPTIONS', 0, 0),
    0x01E2: ('MAIL.SEND.MAILER', 0, 2),
    0x01E5: ('AUTOCORRECT', 0, 2),
    0x01E9: ('POST.DOCUMENT', 0, 1),
    0x01EB: ('PICKLIST', 0, 0),
    0x01ED: ('VIEW.SHOW', 0, 1),
    0x01EE: ('VIEW.DEFINE', 0, 3),
    0x01EF: ('VIEW.DELETE', 0, 1),
    0x01FD: ('SHEET.BACKGROUND', 0, 2),
    0x01FE: ('INSERT.MAP.OBJECT', 0, 0),
    0x01FF: ('OPTIONS.MENONO', 0, 5),
    0x0205: ('MSOCHECKS', 0, 0),
    0x0206: ('NORMAL', 0, 0),
    0x0207: ('LAYOUT', 0, 0),
    0x0208: ('RM.PRINT.AREA', 0, 1),
    0x0209: ('CLEAR.PRINT.AREA', 0, 0),
    0x020A: ('ADD.PRINT.AREA', 0, 0),
    0x020B: ('MOVE.BRK', 0, 4),
    0x0221: ('HIDECURR.NOTE', 0, 2),
    0x0222: ('HIDEALL.NOTES', 0, 1),
    0x0223: ('DELETE.NOTE', 0, 1),
    0x0224: ('TRAVERSE.NOTES', 0, 2),
    0x0225: ('ACTIVATE.NOTES', 0, 2),
    0x026C: ('PROTECT.REVISIONS', 0, 0),
    0x026D: ('UNPROTECT.REVISIONS', 0, 0),
    0x0287: ('OPTIONS.ME', 0, 9),
    0x028D: ('WEB.PUBLISH', 0, 9),
    0x029B: ('NEWWEBQUERY', 0, 1),
    0x02A1: ('PIVOT.TABLE.CHART', 0, 16),
    0x02F1: ('OPTIONS.SAVE', 0, 4),
    0x02F3: ('OPTIONS.SPELL', 0, 12),
    0x0328: ('HIDEALL.INKANNOTS', 0, 1),
}

ERRORS = {0x00: "#NULL!", 0x07: "#DIV/0!", 0x0F: "#VALUE!", 0x17: "#REF!",
          0x1D: "#NAME?", 0x24: "#NUM!", 0x2A: "#N/A", 0x2B: "#GETTING_DATA"}
_BINARY = dict(zip(range(3, 18), ("+", "-", "*", "/", "^", "&", "<", "<=", "=", ">=",
                                       ">", "<>", " ", ",", ":")))


class _Reader:
    """Consume exact formula fields without unbounded scalar or string reads."""

    def __init__(self, data):
        self.data, self.offset = data, 0

    def take(self, size):
        """Read one complete source field or reject its truncation."""
        end = self.offset + size
        if size < 0 or end > len(self.data):
            raise ValueError("truncated Excel formula field at byte %d" % self.offset)
        value, self.offset = self.data[self.offset:end], end
        return value

    def number(self, kind):
        """Decode one checked little-endian scalar."""
        return struct.unpack("<" + kind, self.take(struct.calcsize("<" + kind)))[0]


def _column(number):
    text = ""
    while number >= 0:
        number, remainder = divmod(number, 26)
        text = chr(65 + remainder) + text
        number -= 1
    return text


def _ref(reader, dialect, row, column, *, relative=False):
    """Resolve encoded relative offsets using each dialect's row/column widths."""
    target_row = reader.number("I" if dialect == "biff12" else "H")
    encoded_column = reader.number("H")
    row_relative, col_relative = bool(encoded_column & 0x8000), bool(encoded_column & 0x4000)
    target_column = encoded_column & 0x3FFF
    rows, columns = (1048576, 16384) if dialect == "biff12" else (65536, 256)
    if relative:
        sign = 0x80000000 if dialect == "biff12" else 0x8000
        if row_relative:
            target_row = (row + target_row - (2 * sign if target_row & sign else 0)) % rows
        if col_relative:
            target_column = (column + target_column - (16384 if target_column & 8192 else 0)) % columns
    if target_row >= rows or target_column >= columns:
        raise ValueError("Excel formula reference is outside the source grid")
    return ("" if col_relative else "$") + _column(target_column) + ("" if row_relative else "$") + str(target_row + 1)


def _area(reader, dialect, row, column, *, relative=False):
    row_type = "I" if dialect == "biff12" else "H"
    first_row, last_row = reader.number(row_type), reader.number(row_type)
    first_col, last_col = reader.number("H"), reader.number("H")
    first = _Reader(struct.pack("<" + row_type + "H", first_row, first_col))
    last = _Reader(struct.pack("<" + row_type + "H", last_row, last_col))
    return (_ref(first, dialect, row, column, relative=relative) + ":"
            + _ref(last, dialect, row, column, relative=relative))


def _string(reader, dialect, *, array=False):
    count = reader.number("H" if dialect == "biff12" or array else "B")
    wide = dialect == "biff12" or bool(reader.number("B") & 1)
    text = reader.take(count * (2 if wide else 1)).decode("utf-16le" if wide else "latin1")
    return '"' + text.replace('"', '""') + '"'


def _numeric(value):
    if not math.isfinite(value):
        raise ValueError("non-finite Excel formula number")
    return format(value, ".17g")


def _error(code):
    if code not in ERRORS:
        raise ValueError("unknown Excel formula error 0x%02X" % code)
    return ERRORS[code]


def _array(reader, dialect, maximum):
    """Decode source array constants with element and output bounds before expansion."""
    if dialect == "biff12":
        rows, columns = reader.number("I"), reader.number("I")
    else:
        columns, rows = reader.number("B") + 1, reader.number("H") + 1
    if not rows or not columns or rows * columns > len(reader.data) - reader.offset:
        raise ValueError("invalid or truncated Excel array dimensions")
    output, size = [], 2
    for _ in range(rows):
        values = []
        for _ in range(columns):
            kind = reader.number("B")
            if kind == (0 if dialect == "biff12" else 1):
                value = _numeric(reader.number("d"))
            elif kind == (1 if dialect == "biff12" else 2):
                value = _string(reader, dialect, array=True)
            elif kind == (2 if dialect == "biff12" else 4):
                boolean = reader.number("B")
                if boolean not in (0, 1):
                    raise ValueError("invalid Excel array Boolean")
                value = "TRUE" if boolean else "FALSE"
                reader.take(0 if dialect == "biff12" else 7)
            elif kind == (4 if dialect == "biff12" else 16):
                value = _error(reader.number("B"))
                reader.take(3 if dialect == "biff12" else 7)
            elif kind == 0 and dialect == "biff8":
                reader.take(8)
                value = ""
            else:
                raise ValueError("unsupported Excel array value type 0x%02X" % kind)
            size += len(value) + 1
            if size > maximum:
                raise ValueError("Excel formula output limit exceeded")
            values.append(value)
        output.append(",".join(values))
    return "{" + ";".join(output) + "}"


def _function(reader, stack, variable):
    """Join a verified function or XLM command with its encoded source arguments."""
    count = reader.number("B") & 0x7F if variable else None
    identifier = reader.number("H")
    command = bool(identifier & 0x8000)
    identifier &= 0x7FFF
    if command:
        if identifier not in CETAB or count is None:
            raise ValueError("unknown Excel command 0x%04X" % identifier)
        name, minimum, maximum = CETAB[identifier]
        if count < minimum or maximum is not None and count > maximum:
            raise ValueError("invalid Excel command argument count")
    else:
        if identifier >= len(FTAB) or not FTAB[identifier]:
            raise ValueError("unknown Excel function 0x%04X" % identifier)
        name = FTAB[identifier]
        count = FTAB_ARGC[identifier] if count is None else count
        if not variable and count >= 128:
            raise ValueError("variable Excel function used without encoded argument count")
    if count > len(stack):
        raise ValueError("invalid Excel function argument stack")
    arguments = stack[-count:] if count else []
    consumed = sum(map(len, arguments))
    if count:
        del stack[-count:]
    if not command and identifier == 255:
        if not arguments:
            raise ValueError("Excel UDF has no source function name")
        name, arguments = arguments[0], arguments[1:]
    return name + "(" + ",".join(arguments) + ")", consumed


def _structured_reference(reader, auxiliary, tables, external, maximum):
    """Resolve PtgList only from admitted native table and column identities."""
    scope, flags, identifier, first, last = (reader.number("H"), reader.number("H"),
        reader.number("I"), reader.number("H"), reader.number("H"))
    if flags & 0x1000:
        return "#REF!"
    columns, row_type = flags & 3, (flags >> 2) & 31
    if flags & 0x2000:
        has_columns, extra_flags = auxiliary.number("B"), auxiliary.number("H")
        if has_columns not in (0, 1):
            raise ValueError("invalid external structured-reference column flag")
        count = auxiliary.number("H")
        if not 0 < count < 256:
            raise ValueError("invalid external structured-reference table name length")
        name = auxiliary.take(count * 2).decode("utf-16le")
        row_type = extra_flags & 31
        labels = []
        if has_columns:
            auxiliary.take(2)
            columns = auxiliary.number("B")
            if columns not in (1, 2):
                raise ValueError("invalid external structured-reference column count")
            for index in range(columns):
                not_last, marker = auxiliary.number("B"), auxiliary.number("H")
                if not_last != int(index + 1 < columns) or marker != 2:
                    raise ValueError("invalid external structured-reference column identity")
                length = auxiliary.number("H")
                labels.append(auxiliary.take(length * 2).decode("utf-16le"))
        else:
            columns = 0
        if scope >= len(external) or not external[scope]:
            raise ValueError("unresolved external structured-reference scope")
        if not isinstance(external[scope], str):
            raise ValueError("external structured reference requires an explicit source scope")
        if len(external[scope]) + len(name) + 1 > maximum:
            raise ValueError("Excel formula output limit exceeded")
        name = external[scope] + "!" + name
    else:
        table = tables.get((scope, identifier))
        if table is None:
            raise ValueError("unresolved Excel table %d:%d" % (scope, identifier))
        name = table["name"]
        source_columns = table["columns"]
        if columns not in (0, 1, 2) or columns and not 0 <= first <= last < len(source_columns):
            raise ValueError("invalid Excel structured-reference column bounds")
        labels = [source_columns[first]] if columns == 1 else (
            [source_columns[first], source_columns[last]] if columns == 2 else [])
    if not isinstance(name, str) or not name or any(not isinstance(label, str) for label in labels):
        raise ValueError("structured-reference source has an unreadable table or column name")
    size = len(name) + sum(len(label) + sum(char in "'#[]@" for char in label) + 2 for label in labels)
    if size > maximum:
        raise ValueError("Excel formula output limit exceeded")
    selectors = {0: ("#Data",), 1: ("#All",), 2: ("#Headers",), 4: ("#Data",),
                 6: ("#Headers", "#Data"), 8: ("#Totals",), 12: ("#Data", "#Totals"),
                 16: ("#This Row",)}
    if row_type not in selectors:
        raise ValueError("invalid Excel structured-reference row type")
    escaped = []
    for label in labels:
        escaped.append("[" + "".join("'" + char if char in "'#[]@" else char
                                    for char in label) + "]")
    if columns and row_type in (0, 4):
        return name + (escaped[0] if columns == 1 else "[" + ":".join(escaped) + "]")
    selected = ["[" + item + "]" for item in selectors[row_type]]
    if escaped:
        selected.append(":".join(escaped))
    return name + (selected[0] if len(selected) == 1 else "[" + ",".join(selected) + "]")


def _pivot_item(source, maximum):
    """Keep native cache item names or explicit runtime-dependent item operations."""
    if source.get("kind") != "pivot-item" or not source.get("parts"):
        raise ValueError("unreadable Excel pivot item source")
    parts, dynamic, size = [], False, 0
    for part in source["parts"]:
        field = part["field"]
        if not isinstance(field, str) or len(field) > maximum:
            raise ValueError("Excel formula output limit exceeded")
        if part["context_required"]:
            value = "PIVOT_ITEM(" + repr(field) + "," + str(part["item_index"]) + "," + str(part["flags"]) + ")"
            dynamic = True
        else:
            item = part["item"]
            if not isinstance(item, (str, int, float, bool)) and item is not None:
                value = "PIVOT_ITEM_NATIVE(" + repr(field) + "," + str(part["item_index"]) + ")"
                dynamic = True
            else:
                label = ("TRUE" if item is True else "FALSE" if item is False else
                         "" if item is None else str(item))
                if len(field) + field.count("'") + len(label) + label.count("'") + 6 > maximum:
                    raise ValueError("Excel formula output limit exceeded")
                value = "'" + field.replace("'", "''") + "'['" + label.replace("'", "''") + "']"
        size += len(value)
        if size > maximum:
            raise ValueError("Excel formula output limit exceeded")
        parts.append(value)
    return (parts[0] if len(parts) == 1 else "PIVOT_INTERSECTION(" + ",".join(parts) + ")"), dynamic


def decode_formula(tokens: bytes, extra: bytes = b"", *, dialect: str, row: int = 0,
                   column: int = 0, names=(), external=(), external_names=None,
                   max_characters: int = 1_048_576, tables=None, pivot_names=(),
                   operand_details=None) -> str:
    """Reconstruct source formulas; append native dynamic-scope evidence when requested."""
    if dialect not in ("biff8", "biff12") or max_characters < 1:
        raise ValueError("invalid Excel formula dialect or output allowance")
    reader, auxiliary, stack = _Reader(tokens), _Reader(extra), []
    stack_characters = 0
    external_names = external_names or {}
    tables = tables or {}
    operand_details = [] if operand_details is None else operand_details
    while reader.offset < len(tokens):
        token_offset = reader.offset
        token = reader.number("B")
        base = (token & 0x1F) + 0x20 if 0x20 <= token < 0x80 else token
        value = None
        if token in _BINARY:
            if len(stack) < 2:
                raise ValueError("invalid Excel binary formula stack")
            right, left = stack.pop(), stack.pop()
            stack_characters -= len(left) + len(right)
            value = "(" + left + _BINARY[token] + right + ")"
        elif token in (0x12, 0x13, 0x14, 0x15):
            if not stack:
                raise ValueError("invalid Excel unary formula stack")
            item = stack.pop()
            stack_characters -= len(item)
            value = {0x12: "+" + item, 0x13: "-" + item,
                     0x14: item + "%", 0x15: "(" + item + ")"}[token]
        elif token == 0x16:
            value = ""
        elif token == 0x17:
            value = _string(reader, dialect)
        elif token == 0x18 and dialect == "biff12":
            subtype = reader.number("B")
            if subtype == 0x19:
                value = _structured_reference(reader, auxiliary, tables, external, max_characters)
            elif subtype == 0x1D:
                index = reader.number("I")
                if index >= len(pivot_names) or pivot_names[index] is None:
                    raise ValueError("unresolved Excel pivot name %d" % index)
                name = pivot_names[index]
                if isinstance(name, dict):
                    value, dynamic = _pivot_item(name, max_characters)
                    if dynamic:
                        operand_details.append({"token_offset": token_offset, "scope_kind": "pivot-item",
                                                "pivot_name_index": index, "context_required": True,
                                                "source_parts": name["parts"]})
                elif not isinstance(name, str):
                    raise ValueError("unreadable Excel pivot name %d" % index)
                elif len(name) + name.count("'") + 2 > max_characters:
                    raise ValueError("Excel formula output limit exceeded")
                else:
                    value = "'" + name.replace("'", "''") + "'"
            else:
                raise ValueError("unsupported Excel formula extension 0x%02X" % subtype)
        elif token == 0x1C:
            value = _error(reader.number("B"))
        elif token == 0x1D:
            boolean = reader.number("B")
            if boolean not in (0, 1):
                raise ValueError("invalid Excel formula Boolean")
            value = "TRUE" if boolean else "FALSE"
        elif token == 0x1E:
            value = str(reader.number("H"))
        elif token == 0x1F:
            value = _numeric(reader.number("d"))
        elif base == 0x20:
            reader.take(14 if dialect == "biff12" else 7)
            value = _array(auxiliary, dialect, max_characters)
        elif base in (0x21, 0x22):
            value, consumed = _function(reader, stack, base == 0x22)
            stack_characters -= consumed
        elif base == 0x23:
            index = reader.number("I")
            if not 1 <= index <= len(names):
                raise ValueError("unresolved Excel defined name %d" % index)
            value = names[index - 1]
        elif base == 0x39:
            key = reader.number("H"), reader.number("I")
            if key not in external_names:
                raise ValueError("unresolved Excel external name %d:%d" % key)
            value = external_names[key]
        elif base in (0x24, 0x2C):
            value = _ref(reader, dialect, row, column, relative=base == 0x2C)
        elif base in (0x25, 0x2D):
            value = _area(reader, dialect, row, column, relative=base == 0x2D)
        elif base in (0x3A, 0x3B, 0x3C, 0x3D):
            index = reader.number("H")
            if index >= len(external):
                raise ValueError("unresolved Excel external sheet %d" % index)
            scope = external[index]
            if isinstance(scope, dict):
                scope_kind = scope.get("kind")
                if scope_kind not in ("caller-sheet", "add-in"):
                    raise ValueError("unknown symbolic Excel source scope")
                prefix = {"caller-sheet": "CALLER_SHEET!", "add-in": "ADDIN_SCOPE!"}[scope_kind]
                operand_details.append({"token_offset": token_offset, "scope_index": index,
                                        "scope_kind": scope_kind, "context_required": True,
                                        "original_target": None})
            elif isinstance(scope, str) and scope:
                prefix = scope + "!"
            else:
                raise ValueError("unresolved Excel external sheet %d" % index)
            if base == 0x3A:
                value = prefix + _ref(reader, dialect, row, column)
            elif base == 0x3B:
                value = prefix + _area(reader, dialect, row, column)
            else:
                reader.take((6 if dialect == "biff12" else 4) * (2 if base == 0x3D else 1))
                value = prefix + "#REF!"
        elif base in (0x2A, 0x2B):
            reader.take((6 if dialect == "biff12" else 4) * (2 if base == 0x2B else 1))
            value = "#REF!"
        elif base in (0x26, 0x27, 0x28):
            reader.take(6)
            if base == 0x26:
                count = auxiliary.number("I" if dialect == "biff12" else "H")
                auxiliary.take(count * (16 if dialect == "biff12" else 8))
        elif base == 0x29:
            reader.take(2)
        elif token == 0x19:
            flags, count = reader.number("B"), reader.number("H")
            if flags not in (1, 2, 4, 8, 16, 32, 33, 64, 65, 128):
                raise ValueError("unsupported Excel formula attribute 0x%02X" % flags)
            if flags == 4:
                reader.take(2 * (count + 1))
            if flags == 16:
                if not stack:
                    raise ValueError("invalid Excel SUM attribute stack")
                item = stack.pop()
                stack_characters -= len(item)
                value = "SUM(" + item + ")"
        elif token == 0x01:
            raise ValueError("Excel shared/array anchor must be resolved by the workbook reader")
        else:
            raise ValueError("unsupported Excel formula token 0x%02X" % token)
        if value is not None:
            stack_characters += len(value)
            if stack_characters > max_characters or len(stack) >= max_characters:
                raise ValueError("Excel formula output limit exceeded")
            stack.append(value)
    if len(stack) != 1:
        raise ValueError("invalid Excel formula result stack")
    if auxiliary.offset != len(extra):
        raise ValueError("unconsumed Excel formula auxiliary bytes at byte %d" % auxiliary.offset)
    return stack[0]
