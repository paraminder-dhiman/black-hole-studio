"""Tiny expression builder that turns Python arithmetic into shader math nodes.

    g = Graph(material.node_tree)
    r = g.length(pos)
    t = g.param("PeakTemp", 6500.0) * (r / r_in) ** -0.75

Scalars wrap Math-node outputs, vectors wrap Vector-Math outputs; Python
numbers are folded into socket default values.  Nodes are auto-placed in
columns so generated trees remain inspectable.
"""

import math

class Sock:
    __slots__ = ("g", "socket", "is_vec")

    def __init__(self, g, socket, is_vec=False):
        self.g, self.socket, self.is_vec = g, socket, is_vec

    # Arithmetic
    def __add__(self, o):
        return self.g.add(self, o)

    def __radd__(self, o):
        return self.g.add(o, self)

    def __sub__(self, o):
        return self.g.sub(self, o)

    def __rsub__(self, o):
        return self.g.sub(o, self)

    def __mul__(self, o):
        return self.g.mul(self, o)

    def __rmul__(self, o):
        return self.g.mul(o, self)

    def __truediv__(self, o):
        return self.g.div(self, o)

    def __rtruediv__(self, o):
        return self.g.div(o, self)

    def __neg__(self):
        return self.g.mul(self, -1.0)

    def __pow__(self, o):
        return self.g.math("POWER", self, o)

    def __rpow__(self, o):
        return self.g.math("POWER", o, self)

def _is_vec(x):
    if isinstance(x, Sock):
        return x.is_vec
    return isinstance(x, (tuple, list))

class Graph:
    def __init__(self, tree, x0=0, y0=0):
        self.tree = tree
        self.nodes = tree.nodes
        self.links = tree.links
        self._col = 0
        self._row = 0
        self.x0, self.y0 = x0, y0
        self.params = {}

    # Node placement
    def node(self, bl_idname, **props):
        n = self.nodes.new(bl_idname)
        for k, v in props.items():
            setattr(n, k, v)
        n.location = (self.x0 + self._col * 180, self.y0 - self._row * 160)
        n.hide = True
        self._row += 1
        if self._row > 24:
            self._row = 0
            self._col += 1
        return n

    def next_column(self):
        self._row = 0
        self._col += 1

    def _set(self, inp, value):
        if isinstance(value, Sock):
            self.links.new(value.socket, inp)
        elif value is not None:
            inp.default_value = value

    # Parameters
    def param(self, name, value=0.0):
        """Named Value node; the add-on writes live parameter values into it."""
        if name in self.params:
            return self.params[name]
        n = self.nodes.new("ShaderNodeValue")
        n.name = n.label = name
        n.outputs[0].default_value = value
        n.location = (self.x0 - 400, self.y0 - len(self.params) * 60)
        s = Sock(self, n.outputs[0])
        self.params[name] = s
        return s

    def color_param(self, name, value=(1, 1, 1, 1)):
        if name in self.params:
            return self.params[name]
        n = self.nodes.new("ShaderNodeRGB")
        n.name = n.label = name
        n.outputs[0].default_value = value
        n.location = (self.x0 - 650, self.y0 - len(self.params) * 60)
        s = Sock(self, n.outputs[0], True)
        self.params[name] = s
        return s

    # Scalar math
    def math(self, op, a, b=None, c=None, clamp=False):
        n = self.node("ShaderNodeMath", operation=op, use_clamp=clamp)
        self._set(n.inputs[0], a)
        if b is not None:
            self._set(n.inputs[1], b)
        if c is not None:
            self._set(n.inputs[2], c)
        return Sock(self, n.outputs[0])

    def vmath(self, op, a, b=None, scale=None):
        n = self.node("ShaderNodeVectorMath", operation=op)
        self._set(n.inputs[0], a)
        if b is not None:
            self._set(n.inputs[1], b)
        if scale is not None:
            self._set(n.inputs["Scale"], scale)
        out = n.outputs["Value"] if op in ("DOT_PRODUCT", "LENGTH", "DISTANCE") else n.outputs["Vector"]
        return Sock(self, out, op not in ("DOT_PRODUCT", "LENGTH", "DISTANCE"))

    def add(self, a, b):
        if _is_vec(a) and _is_vec(b):
            return self.vmath("ADD", a, b)
        if _is_vec(a) or _is_vec(b):
            raise TypeError("cannot add scalar and vector")
        return self.math("ADD", a, b)

    def sub(self, a, b):
        if _is_vec(a) and _is_vec(b):
            return self.vmath("SUBTRACT", a, b)
        if _is_vec(a) or _is_vec(b):
            raise TypeError("cannot subtract scalar and vector")
        return self.math("SUBTRACT", a, b)

    def mul(self, a, b):
        if _is_vec(a) and _is_vec(b):
            return self.vmath("MULTIPLY", a, b)
        if _is_vec(a):
            return self.vmath("SCALE", a, scale=b)
        if _is_vec(b):
            return self.vmath("SCALE", b, scale=a)
        return self.math("MULTIPLY", a, b)

    def div(self, a, b):
        if _is_vec(a) and not _is_vec(b):
            return self.vmath("SCALE", a, scale=self.math("DIVIDE", 1.0, b) if isinstance(b, Sock) else 1.0 / b)
        if _is_vec(a) or _is_vec(b):
            return self.vmath("DIVIDE", a, b)
        return self.math("DIVIDE", a, b)

    # Helpers
    def sqrt(self, a):
        return self.math("SQRT", self.max(a, 0.0))

    def sin(self, a):
        return self.math("SINE", a)

    def cos(self, a):
        return self.math("COSINE", a)

    def atan2(self, y, x):
        return self.math("ARCTAN2", y, x)

    def exp(self, a):
        return self.math("EXPONENT", a)

    def log(self, a):
        return self.math("LOGARITHM", self.max(a, 1e-8), math.e)

    def abs(self, a):
        return self.math("ABSOLUTE", a)

    def min(self, a, b):
        return self.math("MINIMUM", a, b)

    def max(self, a, b):
        return self.math("MAXIMUM", a, b)

    def clamp01(self, a):
        # min/max rather than the node's "Clamp" option: Cycles expands clamped
        # Math/Map Range nodes into extra Clamp nodes with late IDs, which makes
        # its SVM compiler hold values on the stack much longer
        return self.min(self.max(a, 0.0), 1.0)

    def clamp(self, a, lo, hi):
        return self.min(self.max(a, lo), hi)

    def lt(self, a, b):
        return self.math("LESS_THAN", a, b)

    def gt(self, a, b):
        return self.math("GREATER_THAN", a, b)

    def fmod(self, a, b):
        return self.math("FLOORED_MODULO", a, b)

    def lerp(self, a, b, t):
        if _is_vec(a) or _is_vec(b):
            return self.add(self.mul(a, 1.0 - t), self.mul(b, t))
        return a + (b - a) * t

    def smoothstep(self, e0, e1, x):
        """Hermite smoothstep, written out (see clamp01 for why not Map Range)."""
        t = self.clamp01((x - e0) / (e1 - e0))
        return t * t * (3.0 - 2.0 * t)

    def dot(self, a, b):
        return self.vmath("DOT_PRODUCT", a, b)

    def cross(self, a, b):
        return self.vmath("CROSS_PRODUCT", a, b)

    def normalize(self, a):
        return self.vmath("NORMALIZE", a)

    def length(self, a):
        return self.vmath("LENGTH", a)

    def xyz(self, v):
        n = self.node("ShaderNodeSeparateXYZ")
        self._set(n.inputs[0], v)
        return tuple(Sock(self, n.outputs[i]) for i in range(3))

    def vec(self, x, y, z):
        n = self.node("ShaderNodeCombineXYZ")
        for i, c in enumerate((x, y, z)):
            self._set(n.inputs[i], c)
        return Sock(self, n.outputs[0], True)

    def transform(self, v, src, dst, kind="POINT"):
        n = self.node("ShaderNodeVectorTransform", vector_type=kind,
                      convert_from=src, convert_to=dst)
        self._set(n.inputs[0], v)
        return Sock(self, n.outputs[0], True)

    def luminance(self, col):
        n = self.node("ShaderNodeRGBToBW")
        self._set(n.inputs[0], col)
        return Sock(self, n.outputs[0])

    def blackbody(self, temp):
        n = self.node("ShaderNodeBlackbody")
        self._set(n.inputs[0], self.max(temp, 800.0))
        return Sock(self, n.outputs[0], True)

    def noise(self, vector, scale=1.0, detail=4.0, roughness=0.5):
        n = self.node("ShaderNodeTexNoise", noise_dimensions="3D")
        self._set(n.inputs["Vector"], vector)
        self._set(n.inputs["Scale"], scale)
        self._set(n.inputs["Detail"], detail)
        self._set(n.inputs["Roughness"], roughness)
        return Sock(self, n.outputs["Fac"])

    def image(self, img, vector):
        n = self.node("ShaderNodeTexImage", interpolation="Linear", extension="EXTEND")
        n.image = img
        self._set(n.inputs["Vector"], vector)
        return Sock(self, n.outputs["Color"], True)