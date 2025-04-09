// NOTE: non-returning methods should return 0 on success, 1 on failure.

#ifdef ARrtGlobals
#pragma rtGlobals=1     // Use modern global access method
#else
#pragma rtGlobals=3     // Use strict wave reference mode
#endif


// Initialize spots to 2 so we can set a probe pos
Function InitProbePos()
    WAVE spotX = root:Packages:MFP3D:Force:SpotX
    WAVE spotY = root:Packages:MFP3D:Force:SpotY
    WAVE spotNum = root:Packages:MFP3D:Force:SpotNum

    // Redimension the spots so they only are of size 2
    // (1st is default and always should be middle of scan).
    Redimension /N=2 spotX
    Redimension /N=2 spotY

    // Indicate that 2nd value in array is what we should move
    // to for spectroscopy.
    spotNum = 1
    return(0)
End Function

Function SetProbePosX(posX)
    Variable posX
    WAVE spotX = root:Packages:MFP3D:Force:SpotX
    spotX[1] = posX
    return(0)
End Function

Function SetProbePosY(posY)
    Variable posY
    WAVE spotY = root:Packages:MFP3D:Force:SpotY
    spotY[1] = posY
    return(0)
End Function

Function GetProbePosX()
    WAVE spotX = root:Packages:MFP3D:Force:SpotX
    return spotX[1]
End Function

Function GetProbePosY()
    WAVE spotY = root:Packages:MFP3D:Force:SpotY
    return spotY[1]
End Function

Function/S GetBaseName()
    SVAR baseName = root:Packages:MFP3D:Main:Variables:BaseName
    return baseName
End Function

Function SetBaseName(newName)
    String newName
    SVAR baseName = root:Packages:MFP3D:Main:Variables:BaseName
    baseName = newName

    // Make Asylum update the suffix
    PV("BaseSuffix", 0)
    ARCheckSuffix()
    ARCheckUserNote(0)  // I'm not sure what this does.
    return(0)
End Function
