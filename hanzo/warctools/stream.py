"""Read records from normal file and compressed file"""

import gzip
import re

from hanzo.warctools.archive_detect import is_gzip_file, guess_record_type

def open_record_stream(record_class=None, filename=None, file_handle=None,
                       mode="rb+", gzip="auto", offset=None, length=None):
    """Can take a filename or a file_handle. Normally called
    indirectly from A record class i.e WarcRecord.open_archive. If the
    first parameter is None, will try to guess"""

    if file_handle is None:
        if filename.startswith('s3://'):
            from . import s3
            file_handle = s3.open_url(filename, offset=offset, length=length)
        else:
            file_handle = open(filename, mode=mode)
            if offset is not None:
                file_handle.seek(offset)

    if record_class == None:
        record_class = guess_record_type(file_handle)

    if record_class == None:
        raise StandardError('Failed to guess compression')

    record_parser = record_class.make_parser()

    if gzip == 'auto':
        if (filename and filename.endswith('.gz')) or is_gzip_file(file_handle):
            gzip = 'record'
            #debug('autodetect: record gzip')
        else:
            # assume uncompressed file
            #debug('autodetected: uncompressed file')
            gzip = None

    if gzip == 'record':
        return GzipRecordStream(file_handle, record_parser)
    elif gzip == 'file':
        return GzipFileStream(file_handle, record_parser)
    else:
        return RecordStream(file_handle, record_parser)


class RecordStream(object):
    """A readable/writable stream of Archive Records. Can be iterated over
    or read_records can give more control, and potentially offset information.
    """
    def __init__(self, file_handle, record_parser):
        self.fh = file_handle
        self.record_parser = record_parser
        self.bytes_to_eor = None

    def seek(self, offset, pos=0):
        """Same as a seek on a file"""
        self.fh.seek(offset, pos)

    def read_records(self, limit=1, offsets=True):
        """Yield a tuple of (offset, record, errors) where
        Offset is either a number or None.
        Record is an object and errors is an empty list
        or record is none and errors is a list"""
        nrecords = 0
        while nrecords < limit or limit is None:
            offset, record, errors = self._read_record(offsets)
            nrecords += 1
            yield (offset, record, errors)
            if not record:
                break

    def __iter__(self):
        while True:
            _, record, errors = self._read_record(offsets=False)
            if record:
                yield record
            elif errors:
                error_str = ",".join(str(error) for error in errors)
                raise StandardError("Errors while decoding %s" % error_str)
            else:
                break

    def _read_record(self, offsets):
        """overridden by sub-classes to read individual records"""
        if self.bytes_to_eor is not None:
            self._skip_to_eor()  # skip to end of previous record
        self.bytes_to_eor = None
        offset = self.fh.tell() if offsets else None
        record, errors, offset = self.record_parser.parse(self, offset)
        return offset, record, errors

    def write(self, record):
        """Writes an archive record to the stream"""
        record.write_to(self)

    def close(self):
        """Close the underlying file handle."""
        self.fh.close()

    def _skip_to_eor(self):
        if self.bytes_to_eor is None:
            raise Exception('bytes_to_eor is unset, cannot skip to end')

        while self.bytes_to_eor > 0:
            read_size = min(CHUNK_SIZE, self.bytes_to_eor)
            buf = self.read(read_size)
            if len(buf) < read_size:
                raise Exception('expected {} bytes but only read {}'.format(read_size, len(buf)))

    def read(self, count):
        result = self.fh.read(count)
        if self.bytes_to_eor is not None:
            self.bytes_to_eor -= len(result)
        return result

    def readline(self):
        result = self.fh.readline()
        if self.bytes_to_eor is not None:
            self.bytes_to_eor -= len(result)
        return result

CHUNK_SIZE = 8192 # the size to read in, make this bigger things go faster.

class GzipRecordStream(RecordStream):
    """A stream to read/write concatted file made up of gzipped
    archive records"""
    def __init__(self, file_handle, record_parser):
        RecordStream.__init__(self, gzip.GzipFile(fileobj=file_handle), record_parser)
        self.raw_fh = file_handle

    def _read_record(self, offsets):
        if self.bytes_to_eor is not None:
            self._skip_to_eor()  # skip to end of previous record
        self.bytes_to_eor = None

        # self.raw_fh.tell() is only accurate when we've just finished reading
        # a gzip member, which should be the case now
        offset = self.raw_fh.tell() if offsets else None

        record, errors, _offset = \
            self.record_parser.parse(self, offset=None)
        return offset, record, errors


class GzipFileStream(RecordStream):
    """A stream to read/write gzipped file made up of all archive records"""
    def __init__(self, file_handle, record):
        RecordStream.__init__(self, gzip.GzipFile(fileobj=file_handle), record)

    def _read_record(self, offsets):
        # no real offsets in a gzipped file (no seperate records)
        return RecordStream._read_record(self, False)

