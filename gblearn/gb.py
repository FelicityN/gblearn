"""Functions and classes for interacting with grain boundaries.
"""
import numpy as np
from collections import OrderedDict
from gblearn import msg
from os import path
from tqdm import tqdm
from quippy.farray import FortranArray

class GrainBoundaryCollection(OrderedDict):
    """Represents a collection of grain boundaries and the unique environments
    between them.

    .. warning:: If you don't specify a path for `store`, any results (such as
      SOAP matrices, ASR, LER, etc. will *not* be saved to disk. Also, they
      won't be loaded from disk if they already exist.

    Args:
        name (str): identifier for this collection.
        root (str): path to the directory where the raw GB atomic descriptions
          are located.
        store (str): path to the :class:`~gblearn.io.ResultStore` root
          directory that this collection's results are stored in. To use a
          memory-only store, leave this as `None`.
        rxgbid (str): regex pattern for extracting the `gbid` for each GB. Any
          files that don't match the regex are automatically excluded. The regex
          should include a named group `(?P<gbid>...)` so that the GB id can be
          extracted correctly. If not specified, the file name is used as the
          `gbid`.
        sortkey (function): when `root` is investigated to load GBs, the file
          names are first sorted; here you can specify a custom sorting
          function.
        reverse (bool): for GB file name sorting (see `sortkey`), whether to
          reverse the order.
        seed (numpy.ndarray): seed SOAP vector for calculating unique LAEs. This
          is usually the SOAP vector of the perfect bulk crystal in the GB.
        soapargs (dict): key-value pairs for constructing the SOAP descriptor.

    Attributes:
        name (str): identifier for this collection.
        root (str): path to the directory where the raw GB atomic descriptions
          are located.
        store (gblearn.io.ResultStore): storage manager for all the
          intermediate results generated by this collection.
        unique (dict): keys are `float` values of `epsilon` for comparing
          environments; values are themselves dictionaries that have keys as
          `tuple` of `(gbid, aid)` with `aid` the id of the atom (row id) in the
          SOAP vector for the GB; value is the SOAP vector already found to be
          unique for that value of `epsilon`.
        equivalent (dict): keys are `float` values of `epsilon` for comparing
          environments; values are themselves dictionaries that have `gbid` keys
          values are a `dict` having linked keys with :attr:`unique` and values
          a list of `aid` in the GB whose LAEs that are equivalent to the unique
          LAE represented by the key.
        soapargs (dict): key-value pairs for constructing the SOAP descriptor.
        properties (dict): keys are property names, values are `dict` keyed by
          `gbid` with values being the property value for each GB.
    """
    def __init__(self, name, root, store=None, rxgbid=None, sortkey=None,
                 reverse=False, seed=None, **soapargs):
        super(GrainBoundaryCollection, self).__init__()
        self.name = name
        self.root = path.abspath(path.expanduser(root))
        
        self._sortkey = sortkey
        """function: when `root` is investigated to load GBs, the file names are first
          sorted; here you can specify a custom sorting function.
        """
        self._reverse = reverse
        """bool: for GB file name sorting (see `sortkey`), whether to reverse
        the order.
        """        
        self._rxgbid = None
        """_sre.SRE_Pattern: compiled regex for the gbid pattern matching
        string.
        """
        self.unique = {}
        self.equivalent = {}
        self.properties = {}
        self.seed = seed
        self.soapargs = soapargs
        
        if rxgbid is not None:
            import re
            self._rxgbid = re.compile(rxgbid)

        #Search for all the GBs in the specified root folder.
        self.gbfiles = OrderedDict()
        self._find_gbs()

        from gblearn.io import ResultStore
        self.store = ResultStore(self.gbfiles.keys(), store, **soapargs)

    def get_property(self, name):
        """Builds a value vector for a property in this collection.

        Args:
            name (str): name of the property to build the vector for.
        """
        values = self.properties[name]
        return np.array([values[gbid] for gbid in self])
        
    def add_property(self, name, filename=None, values=None, colindex=1,
                     delimiter=None, cast=float, skip=0):
        """Adds a property to each GB in the collection from file or an existing
        dictionary.

        .. note:: You must specify either `filename` or `values`.

        Args:
            name (str): name of the property to index under.
            filename (str): path to the file to import from. First column should
              be the `gbid`. Values are taken from `colindex` and `cast` to the
              specified data type.
            values (dict): keys are `gbid`, values are property values.
            colindex (int): index in the text file to extract values from.
            delimiter (str): delimiter to split on for each row in the file.
            cast: function to apply to the value for this property.
            skip (int): number of rows to skip before reading data.
        """
        if values is not None:
            self.properties[name] = values
            return

        #Extract the gbids directly from the first column before we do the array
        #loading.
        pdict = {}
        iskip = 0
        with open(filename) as f:
            for line in f:
                if iskip < skip:
                    continue
                
                if delimiter is None:
                    rvals = line.split()
                else:
                    rvals = line.split(delimiter)

                gbid = rvals[0]
                pval = cast(rvals[colindex])
                pdict[gbid] = pval

        self.properties[name] = pdict
        
    def _find_gbs(self):

        """Finds all the GBs in the root directory using the regex.
        """
        from os import walk
        allfiles = []
        for (dirpath, dirnames, filenames) in walk(self.root):
            allfiles.extend(filenames)
            break

        for fname in sorted(allfiles, key=self._sortkey, reverse=self._reverse):
            if self._rxgbid is not None:
                gbmatch = self._rxgbid.match(fname)
                if gbmatch:
                    try:
                        gbid = gbmatch.group("gbid")
                        self.gbfiles[gbid] = path.join(self.root, fname)
                    except IndexError:# pragma: no cover
                        pass
            else:
                self.gbfiles[fname] = path.join(self.root, fname)

        msg.info("Found {} grain boundaries.".format(len(self.gbfiles)))

    def load(self, parser=None, **kwargs):
        """Loads the GBs from their files to create :class:`GrainBoundary`
        objects.

        .. note:: The :class:`GrainBoundary` objects are stored in this objects
          dictionary (it inherits from :class:`~collections.OrderedDict`). Thus
          :attr:`keys` are the `gbid` and :attr:`values` are the
          :class:`GrainBoundary` instances, in the sorted order that they were
          discovered.

        Args:
            parser: object used to parse the raw GB file. Defaults to
              :class:`gblearn.lammps.Timestep`. Class should have a method `gb`
              that constructs a :class:`GrainBoundary` instance.
            kwargs (dict): keyword arguments passed to the `gb` method of
             `parser`. For example, see :meth:`gblearn.lammps.Timestep.gb`.
        """
        if parser is None:
            from gblearn.lammps import Timestep
            parser = Timestep
        kwargs["soapargs"] = self.soapargs
        kwargs["padding"] = self.soapargs["rcut"]*2
            
        for gbid, gbpath in tqdm(self.gbfiles.items()):
            t = parser(gbpath)
            gb = t.gb(**kwargs)
            self[gbid] = gb

    def soap(self):
        """Calculates the SOAP vector matrix for the atomic environments at
        each grain boundary.
        """
        P = self.store.P

        if len(P) == len(self):
            #No need to recompute if the store has the result.
            return P
            
        for gbid, gb in tqdm(self.items()):
            P[gbid] = gb.soap(cache=False)
            
    @property
    def P(self):
        """Returns the computed SOAP matrices for each GB in the collection.
        """
        result = self.store.P
        if len(result) == 0:
            msg.info("The SOAP matrices haven't been computed yet. Use "
                     ":meth:`soap`.")
            
        return result
            
    @property
    def ASR(self):
        """Returns the ASR for the GB collection.
        """
        result = self.store.ASR
        P = self.P
        
        if result is None and len(P) > 0:
            soaps = []
            for gbid in P.gbids:
                with P[gbid] as Pi:
                    soaps.append(np.sum(Pi, axis=0))

            result = np.vstack(soaps)
            self.store.ASR = result

        return result

    def U(self, eps):
        """Returns the uniquified set of environments for the GB collection and
        current `soapargs`.

        Args:
            eps (float): similarity threshlod parameter.
        """
        result = None
        U = self.store.U
        if eps in U:
            result = U[eps]

        if result is None:
            result = self.uniquify(eps)
            U[eps] = result
            self.store.U = U

	#Just grab the atom ids from each list and then assign that
	#particular atom the corresponding unique signature. Note that
	#each unique signature atom list has the unique signature as the
	#first element, which is why the range starts at 1.
        for gbid in self.gbfiles:
            LAEs = result["GBs"][gbid]
            for u, elist in LAEs.items():
	        for PID, VID in elist[1:]:
                    self[gbid].LAEs[VID] = u
                
        return result
    
    def uniquify(self, eps):
        """Extracts all the unique LAEs in the entire GB system using the
        specified `epsilon` similarity value.

        .. warning:: This method does not verify the completion status of any
          previous :meth:`uniquify` attempts. It just re-runs everything and
          clobbers any existing results for the specified value of `epsilon`.

        Args:
            eps (float): similarity scores below this value are considered
              identical. Two actually identical GBs will have a similarity score
              of `0` by this metric, so smaller is more similar.

        Returns:
            dict: with keys `U` and `GBs`. The `U` key has a dictionary of 
            `(PID, VID)` identifiers for the unique LAEs in the GB collection. The
            values are the corresponding SOAP vectors. `GBs` is a dictionary with
            `gbid` keys and values being a `dict` keyed by unique LAEs with values a
            list of `(PID, VID)` identifiers from the global GB collection.
        """
        from tqdm import tqdm
        result = {
            "U": None,
            "GBs": {}
        }
        
        #We pre-seed the list of unique environments with perfect FCC.
        if self.seed is None:
            raise ValueError("Cannot uniquify LAEs without a seed LAE.")
        
        U = OrderedDict()
	U[('0', 0)] = self.seed
        
	for gbid in tqdm(self.gbfiles):
            with self.P[gbid] as NP:
                self._uniquify(NP, gbid, U, eps)

        #Now that we have the full list of unique environments, go through a
        #second time and classify every vector in each GB.
        used = {k: False for k in U}
	for gbid in tqdm(self.gbfiles):
            with self.P[gbid] as NP:
                LAEs = self._classify(NP, gbid, U, eps, used)      			
            	result["GBs"][gbid] = LAEs

        #Now, remove any LAEs from U that didn't get used. We shouldn't really
        #have many of these.
        for k, v in used.items():
            if not v:# pragma: no cover
                del U[k]
                
        #Populate the result dict with the final unique LAEs. We want to store
        #these ordered by similarity to the seed U.
        from gblearn.soap import S
        from operator import itemgetter
        K = {u: S(v, self.seed) for u, v in U.items()}
        Us = OrderedDict(sorted(K.items(), key=itemgetter(1), reverse=True))
        result["U"] = OrderedDict([(u, U[u]) for u in Us])
        return result
                
    def _uniquify(self, NP, gbid, uni, eps):
        """Runs the first unique identification pass through the collection. Calculates
        the unique SOAP vectors in the given GB relative to the current set of
        unique ones.
        
        .. note:: This version was includes refactoring by Jonathan Priedemann.

        Args:
            NP (numpy.ndarray): matrix of SOAP vectors for the grain boundary.
            gbid (str): id of the grain boundary in the publication set.
            uni (dict): keys are `tuple` of (PID, VID) with `PID` the
              publication Id of the grain boundary and `VID` the id of the SOAP
              vector in that GBs descriptor matrix. Value is the actual SOAP
              vector already found to be unique for some value of `eps`.
            eps (float): cutoff value for deciding whether two vectors are unique.
        
        Returns:
            dict: keys are `tuple` of (PID, VID) linked to `uni`; values are a
            list of `tuple` (PID, VID) of vectors similar to the key.
        """
        from gblearn.soap import S
        for i in range(len(NP)):
	    Pv = NP[i,:]
	    for u in list(uni.keys()):
		uP = uni[u]
		K = S(Pv, uP)
		if K < eps:
                    #This vector already has at least one possible classification
        	    break
	    else:
		uni[(gbid, i)] = Pv

    def _classify(self, NP, PID, uni, eps, used):
        """Runs through the collection a second time to reclassify each
        environment according to the most-similar unique LAE identified in
        :meth:`_uniquify`.
        """
        from gblearn.soap import S
        result = {}
        
	for i in range(len(NP)):
	    Pv = NP[i,:]
	    K0 = 1.0 #Lowest similarity kernel among all unique vectors.
	    U0 = None #Key of the environment corresponding to K0
            
	    for u, uP in uni.items():
		if u not in result:
                    result[u] = [u]
            	K = S(Pv, uP)

		if K < eps:
                    #These vectors are considered to be equivalent. Store the
                    #equivalency in the result.
        	    if K < K0:
			K0 = K
			U0 = u
                
	    if K0 < eps:
		result[U0].append((PID, i))
		used[U0] = True
	    else:# pragma: no cover
                #This is just a catch warning; it should never happen in
                #practice.
                wmsg = "There was an unclassifiable SOAP vector: {}"
                msg.warn(wmsg.format((PID, i)))
                
   	return result

    def features(self, eps):
        """Calculates the feature descriptor for the given `eps` value and
        places it in the store.

        Args:
            eps (float): cutoff value for deciding whether two vectors are
              unique.
        """
        result = None
        features = self.store.features
        if eps in features:
            result = features[eps]

        if result is None:
            U = self.U(eps)            
            result = list(U["U"].keys())
            features[eps] = result
            self.store.features = features
            self._create_feature_map(eps)

        return result
    
    def LER(self, eps):
        """Produces the LAE fingerprint for each GB in the system. The LAE
        figerprint is the percentage of the GBs local environments that belong to
        each unique LAE type.
        
        Args:
            eps (float): cutoff value for deciding whether two vectors are
              unique.

        Returns:
            numpy.ndarray: rows represent GBs; columns are the percentage of unique
              local environments of each type in each GB. 
        """
        result = None
        LER = self.store.LER
        if eps in LER:
            result = LER[eps]

        if result is None:
            U = self.U(eps)
        
            #Next, loop over each GB and count how many of each kind it has.
            result = np.zeros((len(self), len(U["U"])))
            for gbi, gbid in enumerate(self):
                for ui, uid in enumerate(U["U"]):
                    result[gbi,ui] = self[gbid].LAEs.count(uid)
                #Normalize by the total number of atoms of each type
                N = np.sum(result[gbi,:])
                assert N == len(self[gbid])
                result[gbi,:] /= N

            LER[eps] = result
            self.store.LER = LER
            
        return result

    def feature_map_file(self, eps):
        """Returns the full path to the feature map file.

        Args:
            eps (float): `eps` value used in finding the set of unique LAEs in
              the GB system.
        """
        filename = "{0:.5f}-features.dat".format(eps)
        return path.join(self.store.features_, filename)        
    
    def _create_feature_map(self, eps):
        """Creates a feature map file that interoperates with the XGBoost boosters
        dump method.

        .. note:: It is important that the list of features has the *same order* as
          the features in the matrix that the model was trained on.

        Args:
            eps (float): `eps` value used in finding the set of unique LAEs in
              the GB system.
        """
        with open(self.feature_map_file(eps), 'w') as outfile:
            i = 0
            for feat in self.store.features[eps]:
                outfile.write('{0}\t{1}\tq\n'.format(i, feat))
                i = i + 1

    def importance(self, eps, model):
        """Calculates the feature importances based on the specified XGBoost
        model.

        Args:
            eps (float): `eps` value used in finding the set of unique LAEs in
              the GB system.
            model: one of :class:`xgboost.XGBClassifier` or
              :class:`xgboost.XGBRegressor`.
        """
        from gblearn.analysis import order_features_by_gains
        mapfile = self.feature_map_file(eps)
        gains = order_features_by_gains(model.booster(), mapfile)
        return [(g[0], int(g[1])) for g in gains]
                
class GrainBoundary(object):
    """Represents a grain boundary that is defined by a list of atomic
    positions.

    Args:
        xyz (numpy.ndarray): cartesian position of the atoms at the
          boundary. Shape is `(N, 3)`.
        types (numpy.ndarray): of `int` atom types for each atom in the
          `xyz` list.
        box (numpy.ndarray): box dimensions in cartesian directions in
          format `lo` `hi`. Shape `(3, 2)`. Also supports tricilinic boxes when
          shape `(3, 3)` is specified.
        Z (int or list): element code(s) for the atomic species.
        extras (dict): keys are additional atomic attributes; values are lists
          of attribute values. Value arrays must have same length as `xyz`.
        selectargs (dict): keyword arguments passed to the selection routine
          that sliced the GB atoms in the first place. Needed to ensure
          consistency when SOAP matrix is constructed.
        makelat (bool): when True, use the :func:`gblearn.lammps.make_lattice`
          function to construct the lattice from `box`; otherwise, use `box` as
          the lattice.
        soapargs (dict): keyword arguments to pass to the constructor of the
          :class:`~gblearn.soap.SOAPCalculator` that will be used to calculate
          the `P` matrix for this GB.

    Attributes:
        lattice (numpy.ndarray): array of lattice vector for the grain boundary
          box; this includes padding in the x-direction to allow a "surface" for
          the outermost atoms of the GB slice.
        calculator (~gblearn.soap.SOAPCalculator): calculator for getting the
          SOAP vector matrix for this GB.
        Z (int or list): element code(s) for the atomic species.
        P (numpy.ndarray): SOAP vector matrix; shape `(N, S)`, where `N` is the
          number of atoms at the boundary and `S` is the dimensionality of the
          SOAP vector space (which varies with SOAP parameters).
        LAEs (list): of tuple with `(PID, VID)` corresponding to the unique LAE
          number in the collection's global unique set.
    """
    def __init__(self, xyz, types, box, Z, extras=None, selectargs=None,
                 makelat=True, **soapargs):
        from gblearn.soap import SOAPCalculator
        from gblearn.lammps import make_lattice
        self.xyz = xyz.copy()
        self.types = types

        if makelat:
            self.box = box
            self.lattice = make_lattice(box)
        else:
            self.box = None
            self.lattice = box.copy()
            
        self.calculator = SOAPCalculator(**soapargs)
        self.Z = Z
        self.LAEs = None

        #For the selection, if padding is present in the dictionary, reduce the
        #padding by half so that all the atoms at the GB get a full SOAP
        #environment.
        self.selectargs = selectargs
        if "padding" in self.selectargs:
            self.selectargs["padding"] /= 2

        if extras is not None:
            self.extras = extras.keys()
            for k, v in extras.items():
                if not hasattr(self, k):
                    target = v.copy()
                    if isinstance(target, FortranArray):
                        setattr(self, k, v.copy().T)
                    else:
                        setattr(self, k, v.copy())
                else:
                    msg.warn("Cannot set extra attribute `{}`; "
                             "already exists.".format(k))
        else:
            self.extras = []    
        
        self.P = None
        self._atoms = None
        """quippy.atoms.Atoms: representation of the atoms at the boundary that
        is interoperable with QUIP.
        """
        self._NP = None
        """numpy.ndarray: normalized P matrix, where each row is normalized by
        its L2 norm.
        """
        self._K = None
        """numpy.ndarray: matrix of the dot product of every row in :attr:`NP`
        with every other row.
        """
        
    def __len__(self):
        return len(self.xyz)

    @property
    def NP(self):
        """Returns the *normalized* P matrix where each row is normalized by its
        norm.
        """
        if self._NP is None:
            P = self.soap()
            pself = np.array([np.dot(p, p) for p in P])
            self._NP = np.array([P[i,:]/np.sqrt(pself[i])
                                 for i in range(len(P))
                                 if pself[i] > 0])
        return self._NP

    @property
    def K(self):
        """Returns the kernel similarity matrix between for the P matrix of this
        grain boundary.
        """
        if self._K is None:
            NP = self.NP
            self._K = np.dot(NP, NP.T)
        return self._K

    def load(self, attr, filepath):
        """Loads a SOAP matrix `P` for this GB from serialized file.

        Args:
            attr (str): attribute to load from file; one of ['P', 'R'].
            filepath (str): full path to the file to load.
        """
        from os import path
        if path.isfile(filepath):
            setattr(self, attr, np.load(filepath))

    @property
    def gbids(self):
        """Calculates the set of atom ids that fall within the padding/cutoff
        parameters used for initial atom selection.

        Returns:
            numpy.ndarray: of ids in this GBs atoms list that fall within the
            padding constraints of the selection.
        """
        import gblearn.selection as sel
        from functools import partial
        methmap = {
            "median": sel.median,
            "cna": partial(sel.cna_max, coord=0),
            "cna_z": partial(sel.cna_max, coord=2)
        }
        #Use the same selection parameters that were used to construct
        #the GB in the first place. However, the padding parameter will
        #have been updated in the constructor.
        subpar = self.selectargs["pattr"]
        subsel = self.selectargs["method"]
        if subsel in methmap:
            ids = methmap[subsel](self.xyz, getattr(self, subpar),
                                  types=self.types, **self.selectargs)
            return ids
            
    def trim(self, ids=None):
        """Trims the atoms list, types list and any extras in this GB object so
        that it only includes atoms that fall within the cutoff constraints of
        the selection.

        Args:
            ids (numpy.ndarray): of atom ids in the list that should be kept. If
              not provided, it will be calculated from :attr:`gbids`.
        """
        if ids is None:
            ids = self.gbids
            
        #Since we applied padding to the SOAP vectors again, we need
        #to restrict the XYZ coordinates and other extras of the GB
        #to conform to the new size.
        self._atoms = None
        self.xyz = self.xyz[ids,:]
        if self.types is not None:
            self.types = self.types[ids]
        self.LAEs = [(None, None) for i in range(len(self.xyz))]
        for k in self.extras:
            current = getattr(self, k)
            if hasattr(current, "__getitem__"):
                setattr(self, k, np.array(current)[ids])
            
    def soap(self, cache=True):
        """Calculates the SOAP vector matrix for the atomic environments at the
        grain boundary.

        Args:
            cache (bool): when True, cache the resulting SOAP matrix.
        """
        if self.P is None:
            raw = self.calculator.calc(self.atoms, self.Z)
            P = raw["descriptor"]
            self._NP = None
            self._K = None

            if "padding" in self.selectargs:
                ids = self.gbids
                if ids is not None:
                    P = P[ids,:]

            if cache:
                self.P = P
            else:
                return P
                    
        return self.P

    @property
    def atoms(self):
        """Returns an atoms object for the boundary that can be used for
        calculating the SOAP vectors.

        Args:
            Z (int): element code for the atomic species.
        """
        if self._atoms is None:
            from quippy.atoms import Atoms
            a = Atoms(lattice=self.lattice)
            for xyz in self.xyz:
                a.add_atoms(xyz, self.Z)
            self._atoms = a
        return self._atoms
        
    def save_xyz(self, filename, species=None, vacuum=False):
        """Writes the grain boundary atoms to extended XYZ file format
        that can be used with QUIP.

        Args:
            filename (str): name/path to the file to save to.
            species (str): element name.
        """
        from os import path
        filepath = path.abspath(path.expanduser(filename))

        if vacuum:
            #8
            #Lattice="5.44 0.0 0.0 0.0 5.44 0.0 0.0 0.0 5.44" Properties=species:S:1:pos:R:3 Time=0.0
            #Si        0.00000000      0.00000000      0.00000000
            #For the y and z lattice vectors, we just get the box values from
            #the original files. For x, we make the lattice large so that there is a bunch of
            #space around the slice.
            LVs = ' '.join(["{0:.5f} {1:.5f} {2:.5f}".format(*v)
                            for v in self.lattice])
            with open(filepath, 'w') as f:
                f.write("{0:d}\n".format(len(self.xyz)))
                f.write('Lattice="{}" Properties=species:S:1:pos:R:3\n'.format(LVs))
                afmt = "{0}    {1:.5f}    {2:.5f}    {3:.5f}\n"
                for xyz in self.xyz:
                    f.write(afmt.format(species, *xyz))
        else:
            import quippy.cinoutput as qcio
            out = qcio.CInOutputWriter(filepath)
            self.atoms.write(out)
            out.close()                    
