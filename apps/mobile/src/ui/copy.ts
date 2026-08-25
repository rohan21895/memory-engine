const plural = (count: number, one: string, many = `${one}s`) =>
  count === 1 ? one : many;

export const copy = {
  appName: "Photeo",
  trustCue: "ON-DEVICE · PRIVATE",
  privacyShort: "Your photos stay on this phone.",
  common: {
    back: "Back",
    close: "Close",
    closeHint: "Closes this screen",
    tryAgain: "Try again",
    goBack: "Go back",
    goBackHint: "Returns to the previous screen",
    cancel: "Cancel",
    done: "Done",
    notNow: "Not now",
  },
  welcome: {
    eyebrow: "YOUR PHOTOS, BEAUTIFULLY KEPT",
    title: "Your memories deserve an album.",
    helper:
      "Turn the photos on your phone into a beautiful album. Everything happens here — your photos never leave your phone.",
    action: "Get started",
    actionHint: "Opens the album maker",
  },
  start: {
    title: "Make a photo album",
    helper:
      "We’ll help you pick your best photos and turn them into an album — in 3 easy steps.",
    action: "Choose photos",
    actionHint: "Opens the photos saved on this phone",
    otherWays: "Other ways to add photos",
    otherWaysHint: "Shows folder and Google Photos choices",
    sheetTitle: "Other ways to add photos",
    sheetHelper: "Choose one only if your photos are not in your phone gallery.",
    folder: "A folder on my phone",
    folderHint: "Choose a folder you saved on this phone",
    google: "Google Photos",
    googleHint: "Choose photos from your Google account",
    googleUnavailable:
      "Google Photos is not ready on this phone yet. You can still choose photos from your phone or a folder.",
    opening: "Opening your photos…",
    noPhotos: "No photos were chosen. You can try again when you’re ready.",
    pickerError:
      "We couldn’t open those photos. Nothing was changed. Please try again.",
    buildError:
      "We couldn’t finish this album. Your photos are safe. Please go back and try again.",
    dismissMessage: "Dismiss message",
  },
  steps: {
    accessibilityLabel: (activeStep: number) =>
      `Album progress. Step ${activeStep} of 3.`,
    labels: ["Pick", "Review", "Done"],
    step: (step: number) => `Step ${step} of 3`,
  },
  picker: {
    title: "Pick your photos",
    helper:
      "Tap the photos you like. Don’t worry about picking perfectly — we’ll choose the best shots for you.",
    backHint: "Returns to the album start screen",
    selectAll: "Select all",
    selectAllHint: "Selects every photo in the current view",
    clear: "Clear",
    clearHint: "Removes all selected photos",
    filter: "Filter",
    filterHint: "Choose photos by date, album, place, or person",
    filtersApplied: (count: number) =>
      count === 1 ? "1 filter on" : `${count} filters on`,
    tip: "Tip: press and hold, then drag to select many at once.",
    dismissTip: "Hide photo selection tip",
    loadingTitle: "Looking through your photos…",
    loadingHelper: "This may take a moment if you have lots of photos.",
    emptyTitle: "No photos here yet",
    emptyHelper: "Try clearing a filter, or go back and choose another source.",
    permissionTitle: "Please allow photo access",
    permissionHelper:
      "Photeo needs to see your photos to make an album. Tap Open settings, then choose Photos → Allow all.",
    openSettings: "Open settings",
    openSettingsHint: "Opens this phone’s settings for Photeo",
    errorTitle: "We couldn’t show your photos",
    errorHelper: "Your photos are safe. Please go back and try again.",
    selected: (count: number) => `${count} ${plural(count, "photo")} selected`,
    photoLabel: (filename: string, selected: boolean, order?: number) =>
      selected
        ? `${filename}. Selected as photo ${order ?? ""}`.trim()
        : `${filename}. Not selected`,
    photoHint: "Double tap to change this photo’s selection",
    next: (count: number) => `Next · ${count} ${plural(count, "photo")}`,
    nextHint: "Uses these photos to make an album",
    chooseOne: "Tap at least one photo",
    busy: "Selecting your photos…",
  },
  filters: {
    title: "Filter your photos",
    helper: "Choose one group at a time. You can clear everything at the top.",
    all: "All photos",
    allHint: "Clears every photo filter",
    date: "By date",
    album: "By album",
    place: "By place",
    person: "By person",
    categoryHint: (category: string) => `Shows choices for ${category.toLowerCase()}`,
    backToGroups: "Back to filter groups",
    week: "Past week",
    month: "This month",
    year: "This year",
    anyDate: "Any date",
    anyAlbum: "Any album",
    anyPlace: "Any place",
    anyPerson: "Anyone",
    countries: "Countries",
    cities: "Cities",
    scanningPlaces: (percent?: number) =>
      percent === undefined
        ? "Finding places…"
        : `Finding places… ${percent}%`,
    scanningPeople: (percent?: number) =>
      percent === undefined
        ? "Scanning faces…"
        : `Scanning faces… ${percent}%`,
    noChoices: "No choices found here yet.",
    personName: (index: number) => `Person ${index + 1}`,
    photoCount: (count: number) => `${count} ${plural(count, "photo")}`,
    selectedHint: (label: string, selected: boolean) =>
      `${label}. ${selected ? "Selected" : "Not selected"}`,
  },
  building: {
    title: "Making your album…",
    helper:
      "Looking through your photos and picking the best ones. This stays on your phone.",
    stages: [
      "Getting your photos ready",
      "Finding the clearest moments",
      "Putting your album in order",
      "Adding the finishing touches",
    ],
    progress: (percent: number) => `${percent}% finished`,
    accessibilityLabel: (percent: number) => `Making your album. ${percent}% finished.`,
  },
  review: {
    title: "Here’s your album",
    helper:
      "We picked these for you. Happy with them? Make your album — or go back to change your photos.",
    backHint: "Returns to photo selection",
    count: (count: number) => `${count} ${plural(count, "photo")} in your album`,
    reset: "Undo my changes",
    resetHint: (count: number) => `Returns ${count} changed ${plural(count, "photo")} to Photeo’s choice`,
    make: "Make my album",
    makeHint: "Finishes and saves this album on your phone",
    emptyTitle: "There are no album photos yet",
    emptyHelper: "Go back and pick at least one photo to continue.",
    emptyAction: "Back to Pick",
    page: (page: number) => `Page ${page}`,
    yourChoice: "Your choice",
    openPhotoHint: "Opens this album photo full screen",
    why: "Why this photo?",
    whyHint: "Shows why this photo works well in your album",
    chosenFallback: "A lovely moment for this album.",
    changedReason: "You chose this photo for the page.",
    originalPick: "Photeo’s first choice",
    notSafe: "This photo may not fit the page as neatly.",
    alternativeFallback: "Another lovely photo from this moment.",
  },
  reasons: {
    smilingSharp: "Everyone looks happy and the photo is clear.",
    smiling: "Everyone looks happy in this moment.",
    sharp: "This is one of the clearest photos from the moment.",
    eyesOpen: "Everyone’s eyes are open and easy to see.",
    similarBest: "This is the best of several similar photos.",
    screenshot: "This looks like a screen picture, not a camera photo.",
    blinking: "Someone may be blinking in this photo.",
    faceAway: "Someone is turned away in this photo.",
    facesClear: "Everyone is easy to see in this photo.",
    naturalExpression: "The expression feels natural and warm.",
    light: "The light and detail look lovely here.",
    lowerSmile: "A similar photo caught warmer expressions.",
    composition: "This photo fits the page beautifully.",
    story: "This moment helps tell the story of your album.",
    blur: "A similar photo is a little clearer.",
    crop: "This photo needs more room around the subject.",
    coverageTime: "This photo helps cover the whole event.",
    diagnosticUnavailable: "We couldn’t compare fine detail reliably for this photo.",
    differentPlace: "This photo adds a different place to the album.",
    differentPose: "This photo adds a different pose to the album.",
    distinctTake: "This photo keeps the album visually varied.",
    exposureConcern: "The subject’s lighting may be difficult to see clearly.",
    faceCut: "A face is too close to the edge of this frame.",
    focusConcern: "The subject may be out of focus in this photo.",
    neutralChosen: "A strong photo that fits this part of the album.",
    neutralLeftOut: "A similar photo worked a little better here.",
    onlyShotOfPerson: "This photo helps keep everyone in the story.",
    qualityConcern: "This photo had a lower technical quality score.",
    userChoice: "You chose to keep this photo.",
    userExcluded: "You chose not to include this photo.",
  },
  lightbox: {
    close: "Close full screen photo",
    previous: "Previous photo",
    next: "Next photo",
    counter: (current: number, total: number) => `Photo ${current} of ${total}`,
    alternatives: "See other photos",
    alternativesHint: "Shows other photos you can use on this page",
    usePhoto: "Use this photo",
    usePhotoHint: "Places this photo on the album page",
    noPhoto: "No photo to show.",
    previousHint: "Shows the photo before this one",
    nextHint: "Shows the next photo",
  },
  final: {
    celebration: "ALBUM READY · SAVED ON THIS PHONE",
    title: "Your album is ready!",
    helper: (count: number) =>
      `${count} beautiful ${plural(count, "page")} — kept safely on this phone.`,
    back: "Back to review",
    backHint: "Returns to your album review",
    restart: "Make another album",
    restartHint: "Starts a new photo album",
    emptyTitle: "Your album needs photos",
    emptyHelper: "Start again and choose the photos you would like to keep.",
    pageLabel: (page: number) => `Album page ${page}`,
  },
  access: {
    limitedTitle: "Photeo can only see the photos you picked",
    limitedHelper:
      "Android is showing Photeo a short list, not your library. Allow all photos so albums, people, and places can use everything on this phone.",
    limitedShort: "Photeo can only see the photos you picked, not your whole library.",
    limitedAction: "Allow all photos",
    limitedActionHint: "Asks Android for access to every photo",
    settingsAction: "Open settings",
    settingsActionHint: "Opens this phone’s settings for Photeo",
    dismiss: "Hide access message",
    // Filter/People/Places empty states must not read as a broken feature when
    // the real cause is that Android only handed us a handful of photos.
    limitedPeople: "Only the photos you picked were searched, so people may be missing.",
    limitedPlaces: "Only the photos you picked were searched, so places may be missing.",
    noPlacesTitle: "No places found yet",
    noPlacesHelper:
      "Places come from the location saved inside each photo. Photos taken without location, or copied from another device, won’t have one.",
  },
  states: {
    preparing: "Getting Photeo ready…",
    safe: "Nothing is being uploaded.",
    scanningPhotos: (done: number, total: number) =>
      total > 0
        ? `Looking through ${done.toLocaleString()} of ${total.toLocaleString()} photos`
        : "Looking through your photos…",
    scanningFaces: (done: number, total: number) =>
      total > 0
        ? `Grouping faces — ${done.toLocaleString()} of ${total.toLocaleString()} photos`
        : "Grouping faces on this phone…",
  },
} as const;

export type Copy = typeof copy;
