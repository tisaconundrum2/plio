import os
import warnings

import gdal
import numpy as np
import osr
import pvl
from osgeo import ogr

from plio.io import extract_metadata
from plio.utils.utils import find_in_dict

gdal.UseExceptions()

NP2GDAL_CONVERSION = {
  "uint8": 1,
  "int8": 1,
  "uint16": 2,
  "int16": 3,
  "uint32": 4,
  "int32": 5,
  "float32": 6,
  "float64": 7,
  "complex64": 10,
  "complex128": 11,
}

GDAL2NP_CONVERSION = {}

for k, v in iter(NP2GDAL_CONVERSION.items()):
    GDAL2NP_CONVERSION[v] = k

GDAL2NP_CONVERSION[1] = 'int8'


class GeoDataset(object):
    """
    Geospatial dataset object that represents.

    Parameters
    ----------
    file_name : str
                The name of the input image, including its full path.

    Attributes
    ----------

    base_name : str
                The base name of the input image, extracted from the full path.

    bounding_box : object
                 The bounding box of the image in lat/lon space

    geotransform : object
                   Geotransform reference OGR object as an array of size 6 containing the affine
                   transformation coefficients for transforming from raw sample/line to projected x/y.
                   xproj = geotransform[0] + sample * geotransform[1] + line * geotransform[2]
                   yproj = geotransform[3] + sample * geotransform[4] + line * geotransform[5]

    geospatial_coordinate_system : object
                                   Geospatial coordinate system OSR object.

    latlon_extent : list
                    of two tuples containing the latitide/longitude boundaries.
                    This list is in the form [(lowerlat, lowerlon), (upperlat, upperlon)].

    pixel_width : float
                  The width of the image pixels (i.e. displacement in the x-direction).
                  Note: This is the second value geotransform array.

    pixel_height : float
                   The height of the image pixels (i.e. displacement in the y-direction).
                   Note: This is the sixth (last) value geotransform array.

    spatial_reference : object
                        Spatial reference system OSR object.

    standard_parallels : list
                         of the standard parallels used by the map projection found in the metadata
                         using the spatial reference for this GeoDataset.

    unit_type : str
                Name of the unit used by the raster, e.g. 'm' or 'ft'.

    x_rotation : float
                The geotransform coefficient that represents the rotation about the x-axis.
                Note: This is the third value geotransform array.

    xy_extent : list
                of two tuples containing the sample/line boundaries.
                The first value is the upper left corner of the upper left pixel and
                the second value is the lower right corner of the lower right pixel.
                This list is in the form [(minx, miny), (maxx, maxy)].

    xy_corners : list
                 of tuple corner coordinates in the form:
                 [upper left, lower left, lower right, upper right]

    y_rotation : float
                 The geotransform coefficient that represents the rotation about the y-axis.
                 Note: This is the fifth value geotransform array.

    coordinate_transformation : object
                                The coordinate transformation from the spatial reference system to
                                the geospatial coordinate system.

    inverse_coordinate_transformation : object
                                        The coordinate transformation from the geospatial
                                        coordinate system to the spatial reference system.

    scale : tuple
            The name and value of the linear projection units of the spatial reference system.
            This tuple is of type string/float of the form (unit name, value).
            To transform a linear distance to meters, multiply by this value.
            If no units are available ("Meters", 1) will be returned.

    spheroid : tuple
               The spheroid found in the metadata using the spatial reference system.
               This is of the form (semi-major, semi-minor, inverse flattening).

    raster_size : tuple
                  The dimensions of the raster, i.e. (number of samples, number of lines).

    central_meridian : float
                       The central meridian of the map projection from the metadata.

    no_data_value : float
                    Special value used to indicate pixels that are not valid.

    metadata : dict
               A dictionary of available image metadata

    footprint : object
                An OGR footprint object

    """
    def __init__(self, file_name):
        """
        Initialization method to set the file name and open the file using GDAL.

        Parameters
        ----------
        file_name : str
                   The file name to set and open.

        """
        self.file_name = file_name
        self.dataset = gdal.Open(file_name)
        if self.dataset is None:
          raise IOError('File not found :', file_name)

    def __repr__(self):
        return os.path.basename(self.file_name)

    def __reduce__(self):
        return self.__class__, (self.file_name,)

    @property
    def base_name(self):
        if not getattr(self, '_base_name', None):
            self._base_name = os.path.splitext(os.path.basename(self.file_name))[0]
        return self._base_name

    @property
    def geotransform(self):
        """
        Where the array is in the form:
        [top left x, w-e pixel resolution, x-rotation,
        top left y, y-rotation, n-s pixel resolution]
        """
        if not getattr(self, '_geotransform', None):
            self._geotransform = self.dataset.GetGeoTransform()
        return self._geotransform

    @property
    def standard_parallels(self):
        if not getattr(self, '_standard_parallels', None):
            self._standard_parallels = extract_metadata.get_standard_parallels(self.spatial_reference)
        return self._standard_parallels

    @property
    def unit_type(self):
        if not getattr(self, '_unit_type', None):
            self._unit_type = self.dataset.GetRasterBand(1).GetUnitType()
        return self._unit_type

    @property
    def spatial_reference(self):
        if not getattr(self, '_srs', None):
            self._srs = osr.SpatialReference()
            self._srs.ImportFromWkt(self.dataset.GetProjection())
            try:
                self._srs.MorphToESRI()
                self._srs.MorphFromESRI()
            except: pass #pragma: no cover

            #Setup the GCS
            self._gcs = self._srs.CloneGeogCS()
        return self._srs

    @property
    def nbands(self):
        if not getattr(self, '_nbands', None):
            self._nbands = self.dataset.RasterCount
        return self._nbands

    @property
    def geospatial_coordinate_system(self):
        if not getattr(self, '_gcs', None):
            self._gcs = self.spatial_reference.CloneGeogCS()
        return self._gcs

    @property
    def metadata(self):
        if not hasattr(self, '_metadata'):
            try:
                self._metadata = pvl.load(self.file_name)
            except:
                self._metadata = self.dataset.GetMetadata()
        return self._metadata

    @property
    def footprint(self):
        if not hasattr(self, '_footprint'):
            try:
                polygon_pvl = find_in_dict(self.metadata, 'Polygon')
                start_polygon_byte = find_in_dict(polygon_pvl, 'StartByte')
                num_polygon_bytes = find_in_dict(polygon_pvl, 'Bytes')

                # I too dislike the additional open here.  Not sure a good option
                with open(self.file_name, 'r') as f:
                    f.seek(start_polygon_byte - 1)
                    # Sloppy unicode to string because GDAL pukes on unicode
                    stream = str(f.read(num_polygon_bytes))
                    self._footprint = ogr.CreateGeometryFromWkt(stream)
            except:
                self._footprint = None

        return self._footprint

    @property
    def latlon_corners(self):
        if not getattr(self, '_latlon_corners', None):
            pixel_corners = self.xy_corners
            gt = self.geotransform

            self._latlon_corners = []

            for x, y in pixel_corners:
                x, y = self.pixel_to_latlon(x, y)
                self._latlon_corners.append((x, y))
        return self._latlon_corners

    @property
    def xy_corners(self):
        return [(0, 0),
                (0, self.dataset.RasterYSize),
                (self.dataset.RasterXSize,self.dataset.RasterYSize),
                (self.dataset.RasterXSize, 0)]

    @property
    def proj_corners(self):
        # The corner coordinates in projected space
        raise NotImplementedError

    @property
    def latlon_extent(self):
        if not getattr(self, '_latlon_extent', None):
            if self.footprint:
                fp = self.footprint
                # If we have a footprint, do not worry about computing a lat/lon transform
                lowerlat, upperlat, lowerlon, upperlon = fp.GetEnvelope()
                self._footprint = [(upperlat, lowerlon),
                                   (lowerlat, lowerlon),
                                   (lowerlat, upperlon),
                                   (upperlat, upperlon)]
            else:
                self._latlon_extent = []
                for x, y in self.xy_extent:
                    x, y = self.pixel_to_latlon(x,y)

                    self._latlon_extent.append((x,y))
        return self._latlon_extent

    @property
    def xy_extent(self):
        return [(0, 0),
                (self.dataset.RasterXSize, self.dataset.RasterYSize)]

    @property
    def proj_extent(self):
        # The extent in projected space
        raise NotImplementedError

    @property
    def pixel_area(self):
        if not getattr(self, '_pixel_area', None):
            extent = self.xy_extent
            self._pixel_area = extent[1][0] * extent[1][1]

        return self._pixel_area

    @property
    def pixel_width(self):
        if not getattr(self, '_pixel_width', None):
            self._pixel_width = self.geotransform[1]
        return self._pixel_width

    @property
    def pixel_height(self):
        if not getattr(self, '_pixel_height', None):
            self._pixel_height = self.geotransform[5]
        return self._pixel_height

    @property
    def x_rotation(self):
        if not getattr(self, '_x_rotation', None):
            self._x_rotation = self.geotransform[2]
        return self._x_rotation

    @property
    def y_rotation(self):
        if not getattr(self, '_y_rotation', None):
            self._y_rotation = self.geotransform[4]
        return self._y_rotation

    @property
    def coordinate_transformation(self):
        if not getattr(self, '_ct', None):
            self._ct = osr.CoordinateTransformation(self.spatial_reference,
                                                    self.geospatial_coordinate_system)
        return self._ct

    @property
    def inverse_coordinate_transformation(self):
        if not getattr(self, '_ict', None):
            self._ict = osr.CoordinateTransformation(self.geospatial_coordinate_system,
                                                     self.spatial_reference)
        return self._ict

    @property
    def no_data_value(self):
        if not getattr(self, '_no_data_value', None):
            self._no_data_value = self.dataset.GetRasterBand(1).GetNoDataValue()
        return self._no_data_value

    @property
    def scale(self):
        if not getattr(self, '_scale', None):
            unitname = self.spatial_reference.GetLinearUnitsName()
            value = self.spatial_reference.GetLinearUnits()
            self._scale = (unitname, value)
        return self._scale

    @property
    def spheroid(self):
        if not getattr(self, '_spheroid', None):
            self._spheroid = extract_metadata.get_spheroid(self.spatial_reference)
        return self._spheroid

    @property
    def raster_size(self):
        if not getattr(self, '_raster_size', None):
            self._raster_size = (self.dataset.RasterXSize, self.dataset.RasterYSize)
        return self._raster_size

    @property
    def central_meridian(self):
        if not getattr(self, '_central_meridian', None):
            self._central_meridian = extract_metadata.get_central_meridian(self.spatial_reference)
        return self._central_meridian

    def pixel_to_latlon(self, x, y):
        """
        Convert from pixel space (i.e. sample/line) to lat/lon space.

        Parameters
        ----------
        x : float
            x-coordinate to be transformed.
        y : float
            y-coordinate to be transformed.

        Returns
        -------
        lat, lon : tuple
                   (Latitude, Longitude) corresponding to the given (x,y).

        """
        try:
            geotransform = self.geotransform
            x = geotransform[0] + (x * geotransform[1]) + (y * geotransform[2])
            y = geotransform[3] + (x * geotransform[4]) + (y * geotransform[5])
            lon, lat, _ = self.coordinate_transformation.TransformPoint(x, y)
        except:
            lat = lon = None
            warnings.warn('Unable to compute pixel to geographic conversion without '
                          'projection information for {}'.format(self.base_name))

        return lat, lon

    def latlon_to_pixel(self, lat, lon):
        """
        Convert from lat/lon space to pixel space (i.e. sample/line).

        Parameters
        ----------
        lat: float
             Latitude to be transformed.
        lon : float
              Longitude to be transformed.
        Returns
        -------
        x, y : tuple
               (Sample, line) position corresponding to the given (latitude, longitude).

        """
        geotransform = self.geotransform
        upperlat, upperlon, _ = self.inverse_coordinate_transformation.TransformPoint(lon, lat)
        x = (upperlat - geotransform[0]) / geotransform[1]
        y = (upperlon - geotransform[3]) / geotransform[5]
        return x, y

    def read_array(self, band=1, pixels=None, dtype='float32'):
        """
        Extract the required data as a NumPy array

        Parameters
        ----------

        band : int
               The image band number to be extracted as a NumPy array. Default band=1.

        pixels : list
                 [xstart, ystart, xstop, ystop]. Default pixels=None.

        dtype : str
                The NumPy dtype for the output array. Default dtype='float32'.

        Returns
        -------
        array : ndarray
                The dataset for the specified band.

        """
        band = self.dataset.GetRasterBand(band)

        dtype = getattr(np, dtype)

        if not pixels:
            array = band.ReadAsArray().astype(dtype)
        else:
            # Check that the read start is not outside of the image
            xstart, ystart, xextent, yextent = pixels
            if xstart < 0:
                xstart = 0

            if ystart < 0:
                ystart = 0

            xmax, ymax = map(int, self.xy_extent[1])
            if xstart + pixels[2] > xmax:
                xextent = xmax - xstart

            if ystart + pixels[3] > ymax:
                yextent = ymax - ystart

            array = band.ReadAsArray(xstart, ystart, xextent, yextent).astype(dtype)
        return array


def array_to_raster(array, file_name, projection=None,
                    geotransform=None, outformat='GTiff',
                    ndv=None, bittype='GDT_Float64'):
    """
    Converts the given NumPy array to a raster format using the GeoDataset class.

    Parameters
    ----------
    array : ndarray
            The data to be written via GDAL

    file_name : str
                The output file PATH (relative or absolute)

    projection : object
                 A GDAL readable projection object, WKT string, PROJ4 string, etc.
                 Default: None

    geotransform : object
                   A six parameter geotransformation
                   Default:None.

    outformat : str
                A GDAL supported output format
                Default: 'GTiff'.

    ndv : float
          The no data value for the given band.
          Default: None.

    bittype : str
              A GDAL supported bittype, e.g. GDT_Int32
              Default: GDT_Float64
    """

    driver = gdal.GetDriverByName(outformat)
    try:
        y, x, bands = array.shape
        single = False
    except:
        bands = 1
        y, x = array.shape
        single = True

    dataset = driver.Create(file_name, x, y, bands, getattr(gdal, bittype))

    if geotransform:
        dataset.SetGeoTransform(geotransform)

    if projection:
        if isinstance(projection, str):
            dataset.SetProjection(projection)
        else:
            dataset.SetProjection(projection.ExportToWkt())

    if single == True:
        bnd = dataset.GetRasterBand(1)
        if ndv != None:
            bnd.SetNoDataValue(ndv)
        bnd.WriteArray(array)
        dataset.FlushCache()
    else:
        for i in range(1, bands + 1):
            bnd = dataset.GetRasterBand(i)
            if ndv != None:
                bnd.SetNoDataValue(ndv)
            bnd.WriteArray(array[:,:,i - 1])
            dataset.FlushCache()


def match_rasters(match_to, match_from, destination,
                  resampling_method='GRA_Bilinear'):
    """
    Match a source raster to a match raster, including resolution and extent.

    Parameters
    ==========
    match_to : object
               A GeoDataSet object to be matched to

    match_from : object
                 A GeoDataSet object to be clipped

    destination : str
                  PATH where the output will be written

    resampling_method : str
                        Resampling method to use.  Options include:
                        {GRA_NearestNeighbor, GRA_Bilinear (Default),
                        GRA_Cubic, GRA_CubicSpline, GRA_Lanczos, GRA_Average,
                        GRA_Mode}
    """

    import gdalconst  # import here so Sphinx can build the docos, mocking is not working
    # TODO: If a destination is not provided create an in-memory GeoDataSet object
    match_to_srs = match_to.dataset.GetProjection()
    match_to_gt = match_to.geotransform
    width, height = match_to.raster_size

    match_from__srs = match_from.dataset.GetProjection()
    match_from__gt = match_from.geotransform

    dst = gdal.GetDriverByName('GTiff').Create(destination, width, height, 1,
                                               gdalconst.GDT_Float64)
    dst.SetGeoTransform(match_to_gt)
    dst.SetProjection(match_to_srs)

    gdal.ReprojectImage(match_from.dataset, dst, None, None, getattr(gdalconst, resampling_method))
