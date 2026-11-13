"""Road segments for simulated Lucknow reports, stored as (latitude, longitude).

Segment data © OpenStreetMap contributors via OpenFreeMap and OpenMapTiles.
https://opendatacommons.org/licenses/odbl/1-0/
The expanded localities are identified on the 2020 Lucknow expansion map.
"""

from random import Random
from typing import NamedTuple


class DemoSite(NamedTuple):
    name: str
    zone: str
    start: tuple[float, float]
    end: tuple[float, float]

    def sample(self, rng: Random) -> tuple[float, float]:
        position = rng.random()
        lat, lng = self.start
        end_lat, end_lng = self.end
        return round(lat + (end_lat - lat) * position, 6), round(lng + (end_lng - lng) * position, 6)


DEMO_SITES = (
    DemoSite("Hazratganj", "Central", (26.847080, 80.945705), (26.848057, 80.944256)),
    DemoSite("Aminabad", "Central", (26.846444, 80.923786), (26.847817, 80.924754)),
    DemoSite("Chowk", "Central", (26.866803, 80.911292), (26.868176, 80.911163)),
    DemoSite("Charbagh", "Central", (26.832194, 80.921495), (26.832109, 80.919686)),
    DemoSite("Qaiserbagh", "Central", (26.855633, 80.931242), (26.856652, 80.932138)),
    DemoSite("Lalbagh", "Central", (26.851455, 80.939643), (26.850665, 80.940844)),
    DemoSite("Gomti Nagar", "East", (26.853182, 80.998975), (26.853508, 80.997200)),
    DemoSite("Indira Nagar", "East", (26.883069, 80.998844), (26.884694, 80.998893)),
    DemoSite("Vibhuti Khand", "East", (26.858921, 81.005271), (26.858540, 81.007033)),
    DemoSite("Munshipulia", "East", (26.887301, 81.012679), (26.886163, 81.011875)),
    DemoSite("Alambagh", "South", (26.815616, 80.899882), (26.817132, 80.899230)),
    DemoSite("Ashiyana", "South", (26.789334, 80.912466), (26.790773, 80.913306)),
    DemoSite("Telibagh", "South", (26.773780, 80.936188), (26.773684, 80.937996)),
    DemoSite("Krishna Nagar", "South", (26.796471, 80.894462), (26.796211, 80.896250)),
    DemoSite("Aliganj", "North", (26.892301, 80.945534), (26.893649, 80.946548)),
    DemoSite("Mahanagar", "North", (26.872971, 80.950919), (26.872239, 80.950264)),
    DemoSite("Jankipuram", "North", (26.919486, 80.944970), (26.920630, 80.943682)),
    DemoSite("Vikas Nagar", "North", (26.895365, 80.961623), (26.896242, 80.960097)),
    DemoSite("Rajajipuram", "West", (26.846056, 80.884636), (26.846019, 80.886447)),
    DemoSite("Aishbagh", "West", (26.839270, 80.904721), (26.839709, 80.902977)),
    DemoSite("Dubagga", "West", (26.861962, 80.862608), (26.863587, 80.862642)),
    DemoSite("Amausi", "West", (26.765322, 80.886047), (26.764924, 80.886964)),
    DemoSite("Goila", "North", (26.917842, 81.059511), (26.919228, 81.060457)),
    DemoSite("Purseni", "South", (26.711747, 80.982885), (26.711976, 80.984677)),
    DemoSite("Lolai", "East", (26.852412, 81.063507), (26.851537, 81.065034)),
    DemoSite("Maura", "West", (26.906443, 80.843292), (26.906801, 80.843571)),
    DemoSite("Jehta", "West", (26.923243, 80.846482), (26.924548, 80.847563)),
    DemoSite("Ghaila", "North", (26.914436, 80.889496), (26.915039, 80.889169)),
    DemoSite("Sadrauna", "West", (26.814743, 80.851402), (26.813898, 80.852948)),
    DemoSite("Dhawa", "East", (26.905782, 81.062091), (26.906452, 81.060627)),
    DemoSite("Semra", "East", (26.888152, 81.055691), (26.887497, 81.054032)),
    DemoSite("Anora", "West", (26.782826, 80.845792), (26.783748, 80.844301)),
    DemoSite("Sarsava", "East", (26.808171, 80.999075), (26.809493, 80.999982)),
    DemoSite("Bijnor", "South", (26.729005, 80.903825), (26.730194, 80.904087)),
    DemoSite("Shahpur", "East", (26.878742, 81.055895), (26.878244, 81.055761)),
    DemoSite("Malesemau", "East", (26.829892, 81.007347), (26.830013, 81.005541)),
    DemoSite("Raipur", "North", (26.935715, 80.922053), (26.935280, 80.922729)),
)
