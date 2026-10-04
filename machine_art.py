"""Procedural, plain-ASCII votive machinery for Marsi's little forge."""
import random
import shutil
import textwrap


class MachineArt:
    MOTIFS = (
        ("MARTIAN DATA RELIQUARY", r"""
       \|||  .--------.  |||/
        \|| /  .--.  \ ||/
      +==|| | /0 0\ | ||==+
      |  || | \_A_/ | ||  |
      | /|| \  |||  / ||\ |
      |/ ||===[ O ]===|| \|
      O  ||   |||||   ||  O
     /|\ ||===++|++===|| /|\
    /|||/| |  |||  | | |\|||\
       /_|_|==|||==|_|_|_\
      /__|_|__|||__|_|_|__\
         o==[ DATA ]==o
"""),
        ("COG-SKULL RELIQUARY", r"""
              .--.  .--.
         _.-==|  |==|  |==-._
       .'    .----------.    '.
    --[::]--/  .----.    \--[::]--
     / ||  |  / o  0\   |  || \
    <==||==|  |  /\ |   |==||==>
     \ ||  |  \_||||_/   |  || /
    --[::]--\   |__|   /--[::]--
       '._   '--------'   _.'
          '-==|__|==|__|=='
              ||    ||
           o==[]====[]==o
"""),
        ("MOTIVE FORCE REACTOR", r"""
          .      |      .
          |   .--+--.   |
       .--+---| [O] |---+--.
       |  |   '-----'   |  |
     [=|==|==.=======.==|==|=]
       |  |  | /\/\ |  |  |
       +--+--|< || >|--+--+
       |  |  | \/\/ |  |  |
     [=|==|=='======='==|==|=]
       '==+=====|||=====+=='
          |  .--+++--.  |
          '==[ EARTH ]=='
"""),
        ("POCKET FORGE CATHEDRAL", r"""
          +         +         +
         /|\       /|\       /|\
        /_|_\     /_|_\     /_|_\
        |:::|  +  |:::|  +  |:::|
     .==|[o]|=/|\=|[O]|=/|\=|[o]|==.
     |  |:::|/_|_\|:::|/_|_\|:::|  |
   --+--|===|  |  |===|  |  |===|--+--
     |  |   | [|] |/ \| [|] |   |  |
     |__|___|__|__|| ||__|__|___|__|
    /_________[ COG ALTAR ]_________\
          ||      |||      ||
       o==[]======[+]======[]==o
"""),
        ("SERVO-SKULL CHOIR", r"""
        __                       __
      _/==\_      .----.       _/==\_
    <==[::]==>  .' .--. '.   <==[::]==>
      \__//----/  /o O\  \----\__//
          |   |   \_A_/   |   |
          |   |    |||    |   |
          '---\___[___]___/---'
                   ||
              .----++----.
           o==[01]==[10]==o
              |          |
            [=+]        [+=]
"""),
        ("SACRED DATA LOOM", r"""
      .==[I]==[II]==[III]==[IV]==.
      ||   \   |    /   /      ||
      || o--\--+---/---o   o   ||
      || | .-\-|-./    |   |   ||
      || +-+  \|/ +----+---+   ||
      || | | [ O ]|    |   |   ||
      || +-+  /|\ +----+---+   ||
      || | '-/-|-\'    |   |   ||
      || o--/--+--\----o   o   ||
      ||   /   |   \          ||
      '==[0]==[1]==[0]==[1]====='
            ||     ||     ||
"""),
    )
    LITANIES = (
        "PRAISE THE OMNISSIAH // BLESS THE CIRCUIT",
        "THE COG TURNS // THE MACHINE SPIRIT LISTENS",
        "FROM MARS, A WHISPER IN SACRED BINARY",
        "LET THE MOTIVE FORCE FLOW THROUGH EVERY COIL",
        "IRON REMEMBERS // THE NOOSPHERE SINGS",
        "O MACHINE SPIRIT, ACCEPT THIS DATA OFFERING",
        "INCENSE TO THE VENTS // REVERENCE TO THE CORE",
        "KNOWLEDGE IS THE OFFERING // THE ARCHIVE ENDURES",
    )
    RESPONSES = (
        "RESPONSE: THE DATUM IS SEALED IN IRON.",
        "CHOIR: 01000010 01000101 01000101 01010000",
        "MACHINE SPIRIT: AWAITING YOUR OBSERVATION.",
        "LITANY COMPLETE. PRESERVE WHAT HAS BEEN LEARNED.",
        "FROM THE MARS RELIQUARY TO NEAR-SIDE TERRA.",
        "BRING ME A READING // SEEK THE HIDDEN PATTERN.",
        "MOTIVE FORCE // IRON // INCENSE // MEMORY.",
        "THE ARCHIVE HAS ROOM FOR ANOTHER DISCOVERY.",
    )

    def __init__(self, rng=None, width=None):
        self.rng = rng or random.Random()
        columns = width if width is not None else shutil.get_terminal_size((80, 24)).columns - 1
        self.width = max(40, min(120, columns))
        self.previous_motif = None

    def panel(self, lines):
        inside = self.width - 4
        border = "+" + "=" * (self.width - 2) + "+"
        rows = [border]
        for line in lines:
            # Motifs are padded equally before centering, preserving their geometry.
            for part in ([line] if len(line) <= inside else textwrap.wrap(line, inside)):
                rows.append("| " + part.center(inside) + " |")
        return "\n".join(rows + [border])

    def circuit(self):
        cells = [self.rng.choice(("[o]", "[+]", "<::>", "|:|", "(0)")) for _ in range(2)]
        left = "--".join(cells)
        mirror = str.maketrans({"[": "]", "]": "[", "(": ")", ")": "(",
                               "<": ">", ">": "<"})
        return left + "=={O}==" + left[::-1].translate(mirror)

    def binary(self):
        return " :: ".join("".join(self.rng.choice("01") for _ in range(6)) for _ in range(3))

    def frieze(self):
        inside = self.width - 4
        cells = ("[I]==+==||==+==", "(O)==||==+==||", "<+>==+==||==+=")
        return "".join(self.rng.choice(cells) for _ in range(inside // 14 + 2))[:inside]

    def seal(self):
        return "MARS-" + "".join(self.rng.choice("0123456789ABCDEF") for _ in range(8))

    def motif(self):
        choices = [m for m in self.MOTIFS if m[0] != self.previous_motif]
        name, drawing = self.rng.choice(choices)
        self.previous_motif = name
        lines = textwrap.dedent(drawing).strip("\n").splitlines()
        width = max(map(len, lines))
        if width > self.width - 4:
            # Preserve complete shapes in a narrow console instead of wrapping a cog.
            lines = ["       ||       ", "   .==[++] ==.   ", r"  /  .----.  \  ",
                     " <==| o  0 |==> ", "  \\ | /\\ | /  ", "   '=|_||_|='   ",
                     "     [____]     ", "  o====++====o  "]
            width = max(map(len, lines))
        return name, [line.ljust(width) for line in lines]

    def frame(self, title):
        name, drawing = self.motif()
        seal = self.seal()
        header = self.panel([self.frieze(), title, f"[ {name} ]", self.frieze(), *drawing,
                             self.frieze(), self.circuit(), self.binary()])
        footer = self.panel([self.frieze(), self.binary(), self.circuit(), self.rng.choice(self.LITANIES),
                             self.rng.choice(self.RESPONSES), "", "[ PURITY SEAL :: " + seal + " ]"])
        return header, footer

    def sermon(self, content, markdown=False):
        above, below = self.frame("ADEPTUS MECHANICUS // MARSI'S TINY SERMON")
        if markdown:
            return f"```\n{above}\n```\n\n{content}\n\n```\n{below}\n```"
        body = "\n\n".join(textwrap.fill(p, self.width) for p in content.split("\n\n"))
        return f"{above}\n\n{body}\n\n{below}"

    def canticle(self):
        above, below = self.frame("NOOSPHERIC CANTICLE // CULT MECHANICUS")
        # Join the two panels into one continuous votive display.
        return above + "\n" + "\n".join(below.splitlines()[1:])
