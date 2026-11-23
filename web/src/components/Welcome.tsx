/** Przykładowe pytania na start, pogrupowane jak sprawy studenta. Każde jest w zestawach
 *  testowych (evaluation/), więc baza ma na nie odpowiedź; pierwsze w „Dziekanacie”
 *  pokazuje dopytanie o wydział. */
export const EXAMPLE_GROUPS: { title: string; questions: string[] }[] = [
  { title: "Dziekanat", questions: ["Kiedy jest otwarty dziekanat?", "Zgubiłem legitymację, co teraz?"] },
  {
    title: "USOSweb",
    questions: ["Gdzie w USOS-ie zobaczę swoje oceny?", "Jak zapisać się na zajęcia przez USOSweb?"],
  },
  { title: "Stypendia", questions: ["Do kiedy trzeba złożyć wniosek o stypendium socjalne?"] },
  { title: "Akademiki", questions: ["Jak złożyć wniosek o miejsce w akademiku?"] },
  {
    title: "Sieć i konta",
    questions: ["Jak podłączyć się do eduroam?", "Zapomniałem hasła do konta uczelnianego. Co zrobić?"],
  },
];

interface Props {
  disabled: boolean;
  onAsk: (question: string) => void;
}

export function Welcome({ disabled, onAsk }: Props) {
  return (
    <section className="welcome" aria-labelledby="welcome-title">
      <h2 id="welcome-title">Pytania o studia na Uniwersytecie Śląskim</h2>
      <p className="lead">
        Odpowiadam na podstawie stron us.edu.pl, usnet.us.edu.pl i eduroam.us.edu.pl i przy każdej odpowiedzi
        pokazuję, skąd to wiem. Nie mam dostępu do USOS-a ani do Twoich danych.
      </p>
      <h3 className="examples-title">Możesz zacząć od jednego z tych pytań</h3>
      <div className="example-groups">
        {EXAMPLE_GROUPS.map((g) => (
          <section key={g.title} className="example-group">
            <h4>{g.title}</h4>
            <ul>
              {g.questions.map((q) => (
                <li key={q}>
                  <button type="button" className="example" onClick={() => onAsk(q)} disabled={disabled}>
                    {q}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
      <p className="welcome-note">
        To studencki projekt, nie oficjalny serwis uczelni. W sprawach wiążących, jak terminy i opłaty, sprawdź
        stronę źródłową albo zapytaj w dziekanacie.
      </p>
    </section>
  );
}
