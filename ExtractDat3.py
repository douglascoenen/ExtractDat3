#!/usr/bin/env python3.8

"""
Classes for extracting data from Thermo Element ICP Mass Spectrometer dat files
"""

"""
Copyright (c) 2014 Dr. Philip Wenig
Copyright (c) 2015-2018 John H. Hartman

Rewrote to be compatible with Python 3 by Douglas Coenen (2021)

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Lesser General Public License version
2.1, as published by the Free Software Foundation.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU Lesser General Public
License version 2.1 along with this program.
If not, see <http://www.gnu.org/licenses/>.
"""
import struct
from pprint import *
import sys
import os
import optparse
import glob
import datetime
import math
from collections import defaultdict

import tkinter as tk
from tkinter import filedialog as fd

try:
    from tqdm import tqdm
except:
    print("If you want to have loading bars, install the library tqdm using the "
          "following command in your terminal: \n\n pip install tqdm \n\n ")

VERSION = 2.2

HDR_INDEX_OFFSET = 33
HDR_INDEX_LEN = 39
HDR_TIMESTAMP = 40

SCAN_NUMBER = 9
SCAN_DELTA = 7
SCAN_ACF = 12
SCAN_PREV_TIME = 18
SCAN_TIME = 19

def Debug(msg):
    if options.debug:
        print(msg)

KEY_EOS = 0xF # end of scan/acquisition
KEY_EOM = 0x8 # end of mass
KEY_BSCAN = 0xC # B-scan
KEY_B = 0xB # ??
KEY_VOLT = 0x4 # accelerating voltage
KEY_TIME = 0x3 # channel time
KEY_MASS = 0x2 # magnet mass
KEY_DATA = 0x1 # data

DATA_ANALOG = 0x0
DATA_PULSE = 0x1
DATA_FARADAY = 0x8

class DATException(Exception):
    """Base exception class for this module."""
    
    pass

class EOS(DATException):
    """End of scan."""
    
    pass

class NotOpen(DATException):
    """Dat file is not open."""
    
    pass

class UnknownKey(DATException):
    """Unknown tag ("key") in record."""
    
    pass

class UnknownDataType(DATException):
    """Unknown data type in record."""
    
    pass

def _CheckOpen(fd):
    if fd is None:
        raise NotOpen()

class Mass(object):
    """Class for one mass in a scan."""
    
    def __init__(self, scan, fd, offset):
        """Decode the mass information from the scan at the specified file and offset."""
        _CheckOpen(fd)
        self.fd = fd
        self.offset = offset
        self.scan = scan
        self.magnetMass = None
        self.acceleratingVoltage = None
        self.channelTime = None
        self.duration = None
        self.measurements = defaultdict(list)
        fd.seek(offset)
        while True:
            tmp = struct.unpack("<I", fd.read(4))[0]
            key = (tmp & 0xF0000000) >> 28
            value = tmp & 0x0FFFFFFF
            if key == KEY_EOS: # end of scan
                raise EOS()
            elif key == KEY_EOM: # end of mass
                self._SetAttr('duration', value)
                self.size = fd.tell() - self.offset
                break
            elif key == KEY_BSCAN:
                pass # not sure what to do with this
            elif key == KEY_B:
                pass # not sure what to do with this either
            elif key == KEY_VOLT:
                value = scan.edac * 1000.0 / value / 2**18 # TODO: verify this formula
                self.acceleratingVoltage = value
            elif key == KEY_TIME:
                self._SetAttr('channelTime', value)
            elif key == KEY_MASS:
                self.magnetMass = value * 1.0 / 2**18
            elif key == KEY_DATA:
                flag = (value & 0x0F000000) >> 24
                dataType = (value & 0x00F00000) >> 20
                exp = (value & 0x000F0000) >> 16
                value = value & 0x0000FFFF
                if dataType == DATA_ANALOG:
                    value = value << exp
                    if flag != 0:
                        value = -value
                    self.measurements['analog'].append(value)
                elif dataType == DATA_PULSE:
                    value = value << exp
                    if flag != 0:
                        value = -value
                    self.measurements['pulse'].append(value)
                elif dataType == DATA_FARADAY:
                    value = value << exp
                    if flag != 0:
                        value = -value
                    self.measurements['faraday'].append(value)
                else:
                    raise UnknownDataType(str(dataType))
            else:
                raise UnknownKey(str(key))

    def _SetAttr(self, name, value):
        """Set an attribute if it is no already set."""
        if getattr(self, name) is not None:
            raise Exception(name + " is already set")
        else:
            setattr(self, name, value)

class Scan(object):
    """Class for one scan in a dat file."""
    
    def __init__(self, dat, offset):
        """Decode the scan information at the specified file and offset."""
        dat.fd.seek(offset)
        self.headerSize = 47 * 4
        vals = struct.unpack("<%dI" % (self.headerSize / 4), dat.fd.read(self.headerSize))
        self.number = vals[9]
        self.delta = vals[7]
        self.acf = vals[12]
        self.time = vals[19]
        self.fcf = vals[35]
        self.edac = vals[31]
        self._vals = vals
        self.fd = dat.fd
        self.offset = offset + self.headerSize # skip over header
        self.dat = dat

    def __iter__(self):
        """Enable iterating over the masses in a scan."""
        return ScanIterator(self, self.offset)

    def GetMass(self, offset):
        """Create a Mass object from the data at the specified offset."""
        _CheckOpen(self.fd)
        try:
            mass = Mass(self, self.fd, offset)
        except EOS:
            mass = None
        return mass

class ScanIterator(object):
    """Iterate over the masses in a scan."""
    
    def __init__(self, scan, offset):
        _CheckOpen(scan.fd)
        self._scan = scan
        self._offset = offset

    def __next__(self):
        mass = self._scan.GetMass(self._offset)
        if mass is None:
            raise StopIteration
        else:
            self._offset += mass.size
        return mass

class DatFile(object):
    """Class for one dat file."""
    
    def __init__(self, path):
        self.path = path
        self.fd = None
        # Read the header.
        with open(self.path, 'rb') as fd:
            fd.seek(0x10)
            fields = 85
            tmp = fd.read(fields * 4)
            vals = struct.unpack('<%dI' % fields, tmp)
            self.timestamp = vals[HDR_TIMESTAMP]
            self._indexLen = vals[HDR_INDEX_LEN]
            self._indexOffset = vals[HDR_INDEX_OFFSET]
            self._vals = vals
            fd.seek(self._indexOffset + 4)
            bytes = self._indexLen * 4
            self._offsets = struct.unpack("<%dI" % self._indexLen, fd.read(bytes))

    def __iter__(self):
        """Enable iteration over the scans in a dat file."""
        return DatFileIterator(self)

    def Open(self):
        """Open the dat file."""
        self.fd = open(self.path, 'rb')

    def Close(self):
        """Close the dat file."""
        self.fd.close()
        self.fd = None

    def NumScans(self):
        """Return the number of scans in the dat file."""
        return len(self._offsets)
    
    def __len__(self):
        return self.NumScans()

    def GetScan(self, index):
        """Create a Scan object from the data in the index'th scan."""
        _CheckOpen(self.fd)
        if index >= len(self._offsets):
            raise IndexError("Scan index out of range: %d >= %d" % (index, len(self._offsets)))
        else:
            return Scan(self, self._offsets[index])

class DatFileIterator(object):
    """Iterate over the scans in a dat file."""
    
    def __init__(self, dat):
        self._dat = dat
        self._i = 0

    def __next__(self):
        if self._i >= self._dat.NumScans():
            raise StopIteration
        else:
            scan = self._dat.GetScan(self._i)
            self._i += 1
        return scan
    def __len__(self):
        return self._dat.NumScans()

description = \
"""\
Decodes the specified dat files and produces a CSV file of their contents. If a single file is
specified the output file has the same base name with a ".csv" suffix. If multiple files are
specified then in addition to producing an output file for each input file an aggregate output
file is produced containing the output from all of the input files. This aggregate output file
has the same base name as the first file with "combinedXX.csv" appended, where 'XX' is a sequence
number to avoid overwriting existing output files. If a directory is specified then all dat files
in the directory are processed.
"""
def main(args):
    """Stand-alone application."""
    
    # Expand any directories into the dat files they contain. Also keep a list of directories.
    
    dirs = []
    files = []
    for arg in args:
        if os.path.isdir(arg):
            dirs.append(arg)
            files.extend(glob.glob(os.path.join(arg, '*.dat')))
        else:
            files.append(arg)

    # Sort the dat files by their creation time.

    dats = [DatFile(f) for f in files]
    dats = sorted(dats, key=lambda x: getattr(x, 'timestamp'))


    # Determine the output directory.

    outputdir = os.path.expanduser('~/Desktop')
    if len(dirs) == 1:
        outputdir = dirs[0]
    elif len(dirs) == 0:
        # No directory specified, try to infer it from the input files.
        dirs = set()
        for dat in dats:
            d = os.path.split(dat.path)[0]
            dirs.add(d)
        dirs = list(dirs)
        if len(dirs) == 1:
            outputdir = dirs[0]
    combinedOutput = None
    if len(dats) > 1:
        # create combined output file name from first dat file name
        # If more than one file is given, the script will output a combined
        # file with all combined
        base = os.path.splitext(os.path.split(dats[0].path)[1])[0] + 'combined'
        i = 0
        while True:
            path = os.path.join(outputdir, base + '%02d' % i + '.csv')
            if not os.path.exists(path):
                break
            i += 1
        try:
            combinedOutput = open(path, "w")
            print("Writing to", path)
        except:
            combinedOutput = None
    #else:
        #outputfile = os.path.join(outputdir, base + '.csv')
        
    printCombinedHeaders = True
    # Loop through all of the Dat objects (i.e. files)
    # If the tqdm package is installed, use it to create loading bars
    # Otherwise just use the normal objects
    if "tqdm" in globals():
        datsit = tqdm(dats)
    else:
        datsit = dats
        
    for dat in datsit:
        headers = ["Scan", "Time", "ACF"]
        printHeaders = True
        outputfile = os.path.splitext(dat.path)[0] + '.csv'
        with open(outputfile, "w") as output:
            
            dat.Open()   # TODO: context
            # Read elements from FIN2 file if it exists.
            try:
                name = os.path.splitext(dat.path)[0] + '.FIN2'
                with open(name, 'rb') as fin2:
                    for i in xrange(0, 8):
                        line = fin2.readline().strip()
                    elements = line.split(',')[1:]
            except:
                elements = None
            # if options.comments:
            #     output.write(dat.path, dat.timestamp, datetime.datetime.fromtimestamp(dat.timestamp), "\n")
            #     if combinedOutput != None:
            #         # print >> combinedOutput, dat.path, dat.timestamp, datetime.datetime.fromtimestamp(dat.timestamp)
            #         combinedOutput.write(dat.path, dat.timestamp, 
            #                              datetime.datetime.fromtimestamp(dat.timestamp), "\n")
            if "tqdm" in globals():
                scanit =  tqdm(enumerate(dat), total=len(dat), leave=None)
            else:
                scanit = enumerate(dat)
                
            for i, scan in scanit:
                # Loop through the scans of the dat file
                timestamp = dat.timestamp + scan.time / 1000.0
                results = [str(i+1), '%f' % timestamp, '%f' % scan.acf]
                try:
                    for j, mass in enumerate(scan):
                        # Loop through the scan for each mass
                        if printHeaders or printCombinedHeaders:
                            if elements is None:
                                element = "Mass%02d" % (j+1)
                            else:
                                element = elements[j]
                            for t in ['pulse', 'analog']:
                                headers += ["%s%s" % (element, t[0])] * len(mass.measurements[t])
                            headers.append('')
                        for t in ['pulse', 'analog']:
                            results += map(lambda x: str(x) if not str(x).startswith('-') else str(-x)+'*', mass.measurements[t])
                        results.append('')
                except UnknownDataType:
                   # print >> sys.stderr, "Warning: unknown data type 0x%x" % int(e.message)
                   continue
                except UnknownKey:
                   # print >> sys.stderr, "Warning: unknown key 0x%x" % int(e.message)
                   continue

                msg = ",".join(headers) # Create the header of the file
                if printHeaders:
                    output.write(msg + "\n")  # Write the header 
                    printHeaders = False
                if printCombinedHeaders and combinedOutput != None:
                    combinedOutput.write(msg + "\n")
                    printCombinedHeaders = False
                    
                msg = ",".join(results)
                output.write(msg + "\n")  # Write the result to the file! 
                
                if combinedOutput != None:
                    combinedOutput.write(msg + "\n")
            dat.Close()

if __name__ == '__main__':
    """Run this when the script is called"""
    
    tk.Tk().withdraw()  # Empty window of Tk
    rawfiles = fd.askopenfilenames(title="Select the Dat file(s)",
                                           filetypes=[("Dat files", "*.dat")])

    main(rawfiles)